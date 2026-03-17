from datetime import datetime, timedelta

from flask import Flask, request
from flask_cors import CORS
from flask_jwt_extended import JWTManager, get_jwt, create_access_token, get_jwt_identity, set_access_cookies
from flask_talisman import Talisman

from config import GeneralConfig
from database import db_session
from admin import admin_pages
from models import Admin, Student
from student import student_pages

app = Flask(__name__)
app.config.from_object(GeneralConfig)
jwt = JWTManager(app)

# security headers
# Talisman(app)

app.register_blueprint(admin_pages, url_prefix="/api/admin")
app.register_blueprint(student_pages, url_prefix="/api/student")

# 跨域支持（本地开发允许前端 origin；生产环境建议用环境变量配置）
_ALLOWED_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174"}
CORS(
    app,
    origins=list(_ALLOWED_ORIGINS),
    supports_credentials=True,
    expose_headers=['Refresh-Access-Token'],
    allow_headers=['Content-Type', 'Authorization'],
)


@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()


# Register a callback function that takes whatever object is passed in as the
# identity when creating JWTs and converts it to a JSON serializable format.


@jwt.user_identity_loader
def user_identity_lookup(id):
    return id


# Register a callback function that loads a user from your database whenever
# a protected route is accessed. This should return any python object on a
# successful lookup, or None if the lookup failed for any reason (for example
# if the user has been deleted from the database).
@jwt.user_lookup_loader
def user_lookup_callback(_jwt_header, jwt_data):
    identity = jwt_data["sub"]
    role = jwt_data.get('role', 'student')
    if role == "admin":
        return db_session.query(Admin).get(identity)
    else:
        return db_session.query(Student).get(identity)


@app.after_request
def refresh_expiring_jwts(response):
    try:
        exp_timestamp = get_jwt()["exp"]
        now = datetime.now()
        target_timestamp = datetime.timestamp(now + timedelta(minutes=10))
        if target_timestamp > exp_timestamp:
            access_token = create_access_token(identity=get_jwt_identity(), additional_headers={
                "role": get_jwt()['role']
            }, additional_claims={
                "role": get_jwt()['role']
            })
            response.headers['Refresh-Access-Token'] = access_token

        return response
    except (RuntimeError, KeyError):
        return response


@app.route('/')
def hello_world():  # put application's code here
    return 'Hello World!'


if __name__ == '__main__':
    # 使用 5001：macOS 上 5000 常被 AirPlay Receiver 占用，会返回 403
    app.run(debug=True, port=5001)
