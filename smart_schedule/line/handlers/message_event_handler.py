from oauth2client import client
from datetime import datetime, date
from collections import OrderedDict, Counter
import re
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, TemplateSendMessage
)

from smart_schedule.line.module import post_carousel
from smart_schedule.domain.line_templates import (
    AccountRemoveConfirm, EventCreateButtons, ExitConfirm, GroupMenuButtons
)
from smart_schedule.settings import (
    messages, Session, REFRESH_ERROR
)
from smart_schedule.google_calendar import api_manager
from smart_schedule.models import Personal, GroupUser, FreeDay

from . import (
    line_bot_api, reply_google_auth_message, reply_refresh_error_message,
    reply_invalid_credential_error_message, generate_message_from_events
)


class MessageEventHandler:
    message_event_messages = messages['text_messages']['message_event']

    def __init__(self, handler):
        self.handler = handler
        self.cases = []
        self.preexe_cases = []
        self.flag_cases = []
        self.session = Session()

        @self._add_case(text='exit', type=['group', 'room'], preexe=True)
        def exit(event):
            time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            confirm_message = TemplateSendMessage(
                alt_text='Confirm template',
                template=ExitConfirm(time, messages['templates']['exit_confirm'])
            )
            line_bot_api.reply_message(
                event.reply_token,
                confirm_message
            )

        @self._add_case(text='help', type='user', preexe=True)
        def user_help(event):
            reply_text = self.message_event_messages['user_help_message']
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )

        @self._add_case(text='help', type=['group', 'room'], preexe=True)
        def group_help(event):
            reply_text = self.message_event_messages['group_help_message']
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )

        @self._add_case(text='#menu')
        def menu(event, service):
            post_carousel(event.reply_token)

        @self._add_case(
            type=['group', 'room'],
            pattern=r'(ss|smart[\s_-]?schedule|スマートスケジュール)$',
            pattern_flags=re.IGNORECASE
        )
        def group_menu(event, service):
            time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # グループのメニューを表示する
            buttons_template_message = TemplateSendMessage(
                alt_text='Button template',
                template=GroupMenuButtons(
                    time,
                    messages['templates']['group_menu_buttons']
                )
            )
            line_bot_api.reply_message(
                event.reply_token,
                buttons_template_message
            )

        @self._add_case(pattern=r'メンバー登録', type=['group', 'room'])
        def member_register(event, service):
            username = event.message.text.split(maxsplit=1)[1]
            talk_id = self._get_talk_id(event)
            with self.session.begin():
                person = self.session.query(Personal).filter(
                    Personal.user_id == talk_id
                ).one()
                self.session.add(GroupUser(name=username, group_id=person.id))
            reply_text = '{}をグループのメンバーに登録しました'.format(username)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )

        @self._add_case(text='select')
        def select(event, service):
            talk_id = self._get_talk_id(event)
            with self.session.begin():
                person = self.session.query(Personal).filter(
                    Personal.user_id == talk_id
                ).one()
                person.calendar_select_flag = True
                try:
                    calendar_list = api_manager.get_calendar_list(service)
                except client.HttpAccessTokenRefreshError:
                    self.session.delete(person)
                    reply_invalid_credential_error_message(event)
                    return
            reply_text = 'Google Calendar で確認できるカレンダーの一覧です。\n 文字を入力してカレンダーを選択してください'
            for item in calendar_list['items']:
                reply_text += '\n- {}'.format(item['summary'])
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )

        @self._add_case(text='logout')
        def logout(event, service):
            time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            confirm_message = TemplateSendMessage(
                alt_text='Confirm template',
                template=AccountRemoveConfirm(
                    time,
                    messages['templates']['account_remove_confirm']
                )
            )
            line_bot_api.reply_message(
                event.reply_token,
                confirm_message
            )

        @self._add_flag_case(flag='day_flag')
        def day(event, service, person):
            person.day_flag = False
            try:
                days = int(event.message.text)
            except ValueError:
                # TODO 数字ではないメッセージが送られてきたときのメッセージを送る
                return
            try:
                events = api_manager.get_events_after_n_days(
                    service,  person.calendar_id, days
                )
            except client.HttpAccessTokenRefreshError:
                self.session.delete(person)
                reply_invalid_credential_error_message(event)
                return
            reply_text = '{}日後の予定'.format(days)
            reply_text = generate_message_from_events(events, reply_text)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )

        @self._add_flag_case(flag='up_to_day_flag')
        def up_to_day(event, service, person):
            person.up_to_day_flag = False
            try:
                days = int(event.message.text)
            except ValueError:
                # TODO 数字ではないメッセージが送られてきたときのメッセージを送る
                return
            try:
                events = api_manager.get_n_days_events(
                    service,  person.calendar_id, days
                )
            except client.HttpAccessTokenRefreshError:
                self.session.delete(person)
                reply_invalid_credential_error_message(event)
                return
            reply_text = '{}日後までの予定'.format(days)
            reply_text = generate_message_from_events(events, reply_text)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )

        @self._add_flag_case(flag='keyword_flag')
        def keyword(event, service, person):
            person.keyword_flag = False
            keyword = event.message.text
            try:
                events = api_manager.get_events_by_title(service,
                                                         person.calendar_id,
                                                         keyword)
            except client.HttpAccessTokenRefreshError:
                self.session.delete(person)
                reply_invalid_credential_error_message(event)
                return
            reply_text = '{}の検索結果'.format(keyword)
            reply_text = generate_message_from_events(events, reply_text)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )

        @self._add_flag_case(flag='calendar_select_flag')
        def calendar_select(event, service, person):
            try:
                calendar_list = api_manager.get_calendar_list(service)
            except client.HttpAccessTokenRefreshError:
                self.session.delete(person)
                reply_invalid_credential_error_message(event)
                return
            summaries = [item['summary'] for item in calendar_list['items']]
            if event.message.text in summaries:
                person.calendar_select_flag = False
                calendar_id = [
                    item['id'] for item in calendar_list['items']
                    if item['summary'] == event.message.text
                ][0]
                person.calendar_id = calendar_id
                reply_text = 'カレンダーを {} に設定しました'.format(event.message.text)
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=reply_text)
                )
            else:
                person.calendar_select_flag = False
                reply_text = '{} はカレンダーには存在しません'.format(event.message.text)
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=reply_text)
                )

        @self._add_flag_case(flag='adjust_flag')
        def adjust(event, service, person):
            group_users = self.session.query(GroupUser).filter(
                GroupUser.group_id == person.id
            ).all()
            # グループのメンバーをシステムに登録していなかった場合
            if len(group_users) == 0:
                reply_text = 'グループのメンバーを登録してください\n例：メンバー登録 橋本'
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=reply_text)
                )
                person.adjust_flag = False
                return -1

            # グループの予定調整を終了
            if event.message.text == "end":
                time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                reply_text = '空いている日'
                dates = []
                for group_user in group_users:
                    dates.extend([free_day.date for free_day in group_user.free_days])
                    for free_day in group_user.free_days:
                        self.session.delete(free_day)

                date_count_dict = OrderedDict(
                    sorted(Counter(dates).items(), key=lambda x: x[0])
                )
                for i, dic in enumerate(date_count_dict.items()):
                    d, count = dic
                    if i % 3 == 0:
                        reply_text += '\n'
                    else:
                        reply_text += ', '
                    reply_text += '{}/{} {}人'.format(d.month, d.day, count)

                best_date_count = max(date_count_dict.values())
                best_dates = [
                    k for k, v in date_count_dict.items()
                    if v == best_date_count
                ]
                print(len(reply_text))
                buttons_template_message = TemplateSendMessage(
                    alt_text='Button template',
                    # TODO ボタンテンプレートが4つしか受け付けないので4つしか選べない
                    template=EventCreateButtons(
                        time,
                        messages['templates']['event_create_buttons'],
                        reply_text,
                        best_dates[:4]
                    )
                )
                line_bot_api.reply_message(
                    event.reply_token,
                    buttons_template_message
                )
                person.adjust_flag = False
                return -1

            user_names = tuple(group_user.name for group_user in group_users)
            if event.message.text.startswith(user_names):
                split_message = event.message.text.split()
                name = split_message[0]
                group_user = [
                    group_user for group_user in group_users
                    if group_user.name == name
                ][0]
                day_strs = split_message[1:]
                datetimes = [
                    datetime.strptime(day_str, '%m/%d') for day_str in day_strs
                ]
                # TODO 2017 にしちゃってるけどどうにかしないと1年後使えねえや笑
                dates = [
                    date(
                        2017, datetime.month, datetime.day
                    ) for datetime in datetimes
                ]
                free_days = [FreeDay(date, group_user.id) for date in dates]
                self.session.add_all(free_days)
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text='空いている日を保存しました')
                )
                return -1

        @self.handler.add(MessageEvent, message=TextMessage)
        def handle(event):
            print(event)
            for func, text, type, pattern, pattern_flags in self.preexe_cases:
                if self._validate(event, text, type, pattern, pattern_flags):
                    func(event)
                    return

            service = self._get_service(event)
            # リフレッシュエラーが起きた場合、手動でアカウント連携を解除するように促すメッセージを送る
            if service == REFRESH_ERROR:
                reply_refresh_error_message(event)
                return
            # DBに登録されていない場合、認証URLをリプライする
            if service is None:
                reply_google_auth_message(event)
                return

            for func, flag in self.flag_cases:
                talk_id = self._get_talk_id(event)
                # TODO トランザクション開始とコミットのタイミングこれじゃまずい気がする
                self.session.begin()
                person = self.session.query(Personal).filter(
                    Personal.user_id == talk_id
                ).one()
                if getattr(person, flag):
                    func(event, service, person)
                    self.session.commit()
                    return
                self.session.close()

            for func, text, type, pattern, pattern_flags in self.cases:
                if self._validate(event, text, type, pattern, pattern_flags):
                    func(event, service)
                    return

    def _add_case(self, text=None, type=None, pattern=None, pattern_flags=0,
                  preexe=False):
        """A decorator that is used to register a function executing 
        in case of matching given filtering rules.

        :param text: the user message text rule as string or its list
        :param type: the type of `linebot.models.sources` as string or its list
        :param pattern: the pattern matching user message text. 
                        it is evaluated by `re.match` function
        :param pattern_flags: the pattern flags given as an argument 
                              of `re.match` function.
        :param preexe: the boolean indicating whether pre-execute the function.
        """
        def wrapper(func):
            if preexe:
                self.preexe_cases.append((func, text, type, pattern, pattern_flags))
            else:
                self.cases.append((func, text, type, pattern, pattern_flags))
            return func

        return wrapper

    def _add_flag_case(self, flag):
        """A decorator that is used to register a function executing 
        in case of erecting given flags.

        :param flag: the flag of `smart_schedule.models.Personal` as string
        """

        def wrapper(func):
            self.flag_cases.append((func, flag))
            return func

        return wrapper

    @classmethod
    def _validate(cls, event, text, type, pattern, pattern_flags):
        if text is not None:
            if not cls._validate_text(event.message.text, text):
                return False
        if type is not None:
            if not cls._validate_text(event.source.type, type):
                return False
        if pattern is not None:
            if not re.match(pattern, event.message.text, pattern_flags):
                return False
        return True

    @staticmethod
    def _validate_text(actual, expected):
        is_valid = False
        if isinstance(expected, list):
            for text in expected:
                if actual == text:
                    is_valid = True
                    break
        if isinstance(expected, str):
            if actual == expected:
                is_valid = True
        return is_valid

    @staticmethod
    def _get_talk_id(event):
        if event.source.type == 'user':
            talk_id = event.source.user_id
        elif event.source.type == 'group':
            talk_id = event.source.group_id
        elif event.source.type == 'room':
            talk_id = event.source.room_id
        else:
            raise Exception('invalid `event.source`')
        return talk_id

    @classmethod
    def _get_service(cls, event):
        talk_id = cls._get_talk_id(event)
        # google calendar api のcredentialをDBから取得する
        credentials = api_manager.get_credentials(talk_id)
        if credentials == REFRESH_ERROR or credentials is None:
            return credentials
        service = api_manager.build_service(credentials)
        return service