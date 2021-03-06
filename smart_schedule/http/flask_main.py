from flask import request, abort
import flask
from oauth2client import client
import hashlib

from linebot.exceptions import (
    InvalidSignatureError
)

from smart_schedule.settings import (
    hash_env, google_env, Session
)
from smart_schedule.models import Talk

flow = client.OAuth2WebServerFlow(
    client_id=google_env['client_id'], client_secret=google_env['client_secret'],
    scope='https://www.googleapis.com/auth/calendar',
    redirect_uri=google_env['redirect_uri'],
    access_type='offline'
)


class FlaskMain:

    def __init__(self, app, handler):
        self.app = app
        self.handler = handler

        @self.app.route("/callback", methods=['POST'])
        def callback():
            # get X-Line-Signature header value
            signature = request.headers['X-Line-Signature']

            # get request body as text
            body = request.get_data(as_text=True)
            self.app.logger.info("Request body: " + body)

            # handle webhook body
            try:
                self.handler.handle(body, signature)
            except InvalidSignatureError:
                abort(400)
            return 'OK'

        @self.app.route('/')
        def index():
            response = 'Hello, Smart Schedule'
            return response

        @self.app.route('/oauth2')
        def oauth2():
            talk_id = flask.request.args.get('talk_id')
            hash = flask.request.args.get('hash')
            if talk_id is None or hash is None:
                print(talk_id)
                print(hash)
                return 'パラメーターが不足しています'
            m = hashlib.md5()
            m.update(talk_id.encode('utf-8'))
            m.update(hash_env['seed'].encode('utf-8'))
            if hash != m.hexdigest():
                print(m.hexdigest())
                print(hash_env['seed'])
                return '不正なハッシュ値です'
            print(flask.session)
            flask.session['talk_id'] = talk_id
            print('saved session')
            print(flask.session)
            auth_uri = flow.step1_get_authorize_url()
            return flask.redirect(auth_uri)

        @self.app.route('/oauth2callback')
        def oauth2callback():
            print(flask.session)
            session = Session()
            if 'talk_id' not in flask.session:
                return '不正なアクセスです。'
            talk_id = flask.session.pop('talk_id')
            auth_code = flask.request.args.get('code')
            credentials = flow.step2_exchange(auth_code)
            with session.begin():
                if session.query(Talk).filter(
                    Talk.talk_id == talk_id
                ).one_or_none() is None:
                    session.add(Talk(
                        talk_id=talk_id, credential=credentials.to_json()
                    ))
                    return 'あなたのLineとGoogleカレンダーが正常に紐付けられました。'
                else:
                    return '既にグループにGoogleアカウントが紐付けられています'
