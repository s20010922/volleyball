import hashlib
import hmac
import os
import secrets
from datetime import datetime

import click
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, insert, inspect, literal, select, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///volleyball.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

admin_plain_pwd = os.getenv('ADMIN_PWD')
if not admin_plain_pwd:
    raise RuntimeError("ADMIN_PWD 未設定，請在 .env 填入後台管理密碼")
ADMIN_PWD_HASH = generate_password_hash(admin_plain_pwd)

DATE_FORMAT = "%Y-%m-%dT%H:%M"  # 前端 <input type="datetime-local"> 的格式

# ==========================================
# 資料庫模型
# ==========================================
class Config(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    announcement = db.Column(db.Text, default="目前尚無公告")

class Event(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    date = db.Column(db.DateTime, nullable=False)
    title = db.Column(db.String(100), nullable=False)
    max_capacity = db.Column(db.Integer, default=0)
    desc = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="active")
    options = db.Column(db.Text, default="")

class Registration(db.Model):
    # 同一場次同一姓名只能有一筆：由資料庫擋重複報名，連點兩次或同時送出都不會重複
    __table_args__ = (db.Index('uq_registration_event_name', 'event_id', 'name', unique=True),)
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    balls = db.Column(db.Integer, default=0)
    note = db.Column(db.Text, default="")
    timestamp = db.Column(db.DateTime, default=datetime.now)
    # 取消碼的 SHA-256；取消碼本身只在報名成功時回給報名者的瀏覽器
    cancel_token_hash = db.Column(db.String(64))

class Vote(db.Model):
    __table_args__ = (db.Index('uq_vote_event_name', 'event_id', 'name', unique=True),)
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    choice = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    cancel_token_hash = db.Column(db.String(64))

class Archive(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    date = db.Column(db.DateTime, nullable=False)
    title = db.Column(db.String(100), nullable=False)
    max_capacity = db.Column(db.Integer, default=0)
    desc = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="archive")
    members = db.Column(db.Text, default="")

def migrate_schema():
    """建表，並把舊版資料庫補上新欄位與唯一索引（可重複執行）。"""
    db.create_all()
    for table in ('registration', 'vote'):
        columns = {c['name'] for c in inspect(db.engine).get_columns(table)}
        if 'cancel_token_hash' not in columns:
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN cancel_token_hash VARCHAR(64)"))
            except Exception:
                # 服務和排程同時第一次啟動時，另一邊可能已經加好了
                if 'cancel_token_hash' not in {c['name'] for c in inspect(db.engine).get_columns(table)}:
                    raise
        try:
            with db.engine.begin() as conn:
                conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_event_name ON {table} (event_id, name)"))
        except Exception:
            app.logger.exception("無法在 %s 建立唯一索引（可能已有重複資料）", table)

# 用 gunicorn 啟動時不會跑 __main__，所以在載入時建表（已存在的表不會動）
with app.app_context():
    migrate_schema()

# ==========================================
# 輔助函式
# ==========================================
class InputError(ValueError):
    """使用者輸入不合法；訊息會直接顯示給使用者。"""

def verify_admin(password):
    if not password: return False
    return check_password_hash(ADMIN_PWD_HASH, str(password))

def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()

def token_matches(token, token_hash):
    if not token or not token_hash: return False
    return hmac.compare_digest(hash_token(str(token)), token_hash)

def get_text(data, key, label, max_len, required=False):
    value = str(data.get(key) or '').strip()
    if required and not value:
        raise InputError(f"請填寫{label}")
    if len(value) > max_len:
        raise InputError(f"{label}最多 {max_len} 個字")
    return value

def get_int(data, key, label, low, high, default=0):
    raw = data.get(key)
    if raw in (None, ''):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise InputError(f"{label}請填數字")
    if not low <= value <= high:
        raise InputError(f"{label}請填 {low} 到 {high} 之間")
    return value

def get_date(data, key, label):
    try:
        return datetime.strptime(str(data.get(key) or ''), DATE_FORMAT)
    except ValueError:
        raise InputError(f"{label}格式不正確")

def get_event_id(data):
    return str(data.get('event_id') or data.get('eventId') or '').strip()

def new_event_id():
    # 一律由伺服器產生，不接受前端指定，避免撞到既有場次
    eid = f"E{int(datetime.now().timestamp() * 1000)}"
    while db.session.get(Event, eid) or db.session.get(Archive, eid):
        eid = f"E{int(eid[1:]) + 1}"
    return eid

def format_event_output(event, is_archived=False):
    return {
        "event_id": event.id,
        "date": event.date.strftime("%Y-%m-%d %H:%M:%S") if event.date else "",
        "title": event.title,
        "max": event.max_capacity,
        "desc": event.desc,
        "status": event.status,
        "options": event.members if is_archived else event.options,
        "isArchived": is_archived
    }

def archive_event(event):
    """把場次搬進 Archive（保留最終名單），並清掉它的報名與投票紀錄。
    自動到期、手動封存、投票無人參與都走這裡，結果一致。"""
    regs = Registration.query.filter_by(event_id=event.id).order_by(Registration.id).all()
    if not db.session.get(Archive, event.id):
        db.session.add(Archive(
            id=event.id,
            date=event.date,
            title=event.title,
            max_capacity=event.max_capacity,
            desc=event.desc,
            status='archive',
            members=", ".join(r.name for r in regs)
        ))
    Registration.query.filter_by(event_id=event.id).delete()
    Vote.query.filter_by(event_id=event.id).delete()
    db.session.delete(event)

def auto_process_finished_votes():
    now = datetime.now()
    events = Event.query.filter(Event.status == 'vote', Event.date <= now).all()
    for event in events:
        votes = Vote.query.filter_by(event_id=event.id).all()
        if votes:
            stats = {}
            for v in votes:
                stats[v.choice] = stats.get(v.choice, 0) + 1
            # 找出最高票（同票時取最早被投的選項）
            winner_date_str = sorted(stats.keys(), key=lambda k: stats[k], reverse=True)[0]

            try:
                final_date = datetime.strptime(winner_date_str, DATE_FORMAT)
            except ValueError:
                final_date = event.date

            event.date = final_date
            event.status = 'active'
            Vote.query.filter_by(event_id=event.id).delete()
        else:
            archive_event(event)
    db.session.commit()
    return len(events)

def auto_archive_events():
    now = datetime.now()
    # 'archive' 是舊版手動封存留在 Event 表裡的場次，一併搬進 Archive
    events = Event.query.filter(
        (Event.status.in_(['active', 'vote']) & (Event.date < now)) | (Event.status == 'archive')
    ).all()
    for event in events:
        archive_event(event)
    db.session.commit()
    return len(events)

@app.cli.command('maintenance')
def maintenance_command():
    """定時維護：結算到期的投票、封存過期場次。由 cron 每分鐘執行。"""
    voted = auto_process_finished_votes()
    archived = auto_archive_events()
    if voted or archived:
        click.echo(f"[{datetime.now():%F %T}] 結算投票 {voted} 場、封存 {archived} 場")

def check_cancel_permission(data, token_hash):
    """取消報名／撤回投票：要有報名時拿到的取消碼，或是管理員。"""
    password = data.get('adminPassword')
    if password:
        if not verify_admin(password):
            # 與其他管理動作一樣回 403，讓 fail2ban 也能擋住用這裡猜密碼
            return jsonify({"status": "fail", "message": "管理密碼錯誤"}), 403
        return None
    if token_matches(data.get('cancel_token'), token_hash):
        return None
    return jsonify({"status": "fail", "message": "只能在報名時使用的手機或電腦上取消。若換了裝置，請聯絡管理員協助。"})

# ==========================================
# API 路由
# ==========================================
@app.route('/api', methods=['GET'])
def get_data():
    # 只讀不寫：到期結算與封存改由 cron 執行 `flask --app app maintenance`
    try:
        config = Config.query.first()
        active_events = Event.query.all()
        archived_events = Archive.query.all()
        votes = Vote.query.all()
        regs = Registration.query.all()

        all_events = [format_event_output(e, False) for e in active_events] + \
                     [format_event_output(a, True) for a in archived_events]

        return jsonify({
            "status": "success",
            "announcement": config.announcement if config else "",
            "events": all_events,
            "votes": [[v.event_id, v.name, v.choice] for v in votes],
            "regs": [[r.event_id, r.name, r.balls, r.note] for r in regs]
        })
    except Exception:
        app.logger.exception("GET /api 失敗")
        return jsonify({"status": "error", "message": "讀取資料失敗，請稍後再試"}), 500

@app.route('/api', methods=['POST'])
def handle_post():
    action_type = None
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "資料格式錯誤"}), 400

        action_type = data.get('type')
        admin_actions = ['verify', 'updateAnnouncement', 'createEvent', 'deleteEvent', 'convertToActive', 'archiveEvent']

        if action_type in admin_actions:
            if not verify_admin(data.get('adminPassword')):
                return jsonify({"status": "fail", "message": "管理密碼錯誤"}), 403

        if action_type == 'verify':
            return jsonify({"status": "success"})

        elif action_type == 'updateAnnouncement':
            content = get_text(data, 'content', '公告', 5000)
            config = Config.query.first()
            if config is None:  # 全新資料庫還沒有公告列
                config = Config()
                db.session.add(config)
            config.announcement = content
            db.session.commit()
            return jsonify({"status": "success"})

        elif action_type == 'createEvent':
            status = data.get('status', 'active')
            if status not in ('active', 'vote'):
                raise InputError("場次類型不正確")

            new_event = Event(
                id=new_event_id(),
                date=get_date(data, 'date', '日期／截止時間'),
                title=get_text(data, 'title', '標題', 100, required=True),
                max_capacity=get_int(data, 'max', '人數上限', 0, 99, default=get_int(data, 'maxCount', '人數上限', 0, 99)),
                desc=get_text(data, 'desc', '說明', 2000),
                status=status,
                options=get_text(data, 'options', '投票選項', 1000, required=(status == 'vote'))
            )
            db.session.add(new_event)
            db.session.commit()
            return jsonify({"status": "success", "id": new_event.id})

        elif action_type == 'vote':
            eid = get_event_id(data)
            name = get_text(data, 'name', '姓名', 50, required=True)
            choice = get_text(data, 'choice', '選項', 100, required=True)

            event = db.session.get(Event, eid)
            if not event or event.status != 'vote':
                return jsonify({"status": "fail", "message": "找不到投票中的場次"})
            if event.date <= datetime.now():
                return jsonify({"status": "fail", "message": "投票已截止"})
            if choice not in [o.strip() for o in (event.options or '').split(',')]:
                return jsonify({"status": "fail", "message": "沒有這個選項"})

            token = secrets.token_urlsafe(16)
            db.session.add(Vote(event_id=eid, name=name, choice=choice, cancel_token_hash=hash_token(token)))
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                return jsonify({"status": "fail", "message": "您已投過票囉！"})
            return jsonify({"status": "success", "cancelToken": token})

        elif action_type == 'register':
            eid = get_event_id(data)
            name = get_text(data, 'name', '姓名', 50, required=True)
            balls = get_int(data, 'balls', '攜帶球數', 0, 10)
            note = get_text(data, 'note', '備註', 200)

            event = db.session.get(Event, eid)
            if not event or event.status != 'active':
                return jsonify({"status": "fail", "message": "找不到開放報名的場次"})
            if event.date <= datetime.now():
                return jsonify({"status": "fail", "message": "報名已截止"})

            # 「數人數」和「新增」在同一道 SQL 完成：SQLite 執行寫入時會鎖住資料庫，
            # 兩人同時搶最後一個名額，後到的那個會重新數到已滿而新增 0 筆
            token = secrets.token_urlsafe(16)
            current_count = select(func.count()).select_from(Registration) \
                .where(Registration.event_id == eid).scalar_subquery()
            stmt = insert(Registration).from_select(
                ['event_id', 'name', 'balls', 'note', 'timestamp', 'cancel_token_hash'],
                select(literal(eid), literal(name), literal(balls), literal(note),
                       literal(datetime.now(), db.DateTime), literal(hash_token(token)))
                .where(current_count < event.max_capacity)
            )
            try:
                inserted = db.session.execute(stmt).rowcount
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                return jsonify({"status": "fail", "message": "您已經報名過此場次囉！"})
            if not inserted:
                return jsonify({"status": "fail", "message": "名額已滿！"})
            return jsonify({"status": "success", "cancelToken": token})

        elif action_type == 'cancelRegistration':
            eid = get_event_id(data)
            name = get_text(data, 'name', '姓名', 50, required=True)

            reg = Registration.query.filter_by(event_id=eid, name=name).first()
            if not reg:
                return jsonify({"status": "fail", "message": "找不到該姓名的報名紀錄，請檢查輸入是否正確。"})
            denied = check_cancel_permission(data, reg.cancel_token_hash)
            if denied:
                return denied
            db.session.delete(reg)
            db.session.commit()
            return jsonify({"status": "success"})

        elif action_type == 'cancelVote':
            eid = get_event_id(data)
            name = get_text(data, 'name', '姓名', 50, required=True)

            vote = Vote.query.filter_by(event_id=eid, name=name).first()
            if not vote:
                return jsonify({"status": "fail", "message": "找不到您的投票紀錄，請檢查姓名是否正確。"})
            denied = check_cancel_permission(data, vote.cancel_token_hash)
            if denied:
                return denied
            db.session.delete(vote)
            db.session.commit()
            return jsonify({"status": "success"})

        elif action_type == 'archiveEvent':
            event = db.session.get(Event, get_event_id(data))
            if event:
                archive_event(event)
                db.session.commit()
                return jsonify({"status": "success"})
            return jsonify({"status": "fail", "message": "找不到場次"})

        elif action_type == 'convertToActive':
            event = db.session.get(Event, get_event_id(data))
            if event:
                if data.get('newDate'):
                    event.date = get_date(data, 'newDate', '日期')
                if data.get('newTitle'):
                    event.title = get_text(data, 'newTitle', '標題', 100)
                event.status = 'active'
                Vote.query.filter_by(event_id=event.id).delete()
                db.session.commit()
                return jsonify({"status": "success"})
            return jsonify({"status": "fail", "message": "找不到場次"})

        elif action_type == 'deleteEvent':
            eid = get_event_id(data)

            # 1. 安全刪除一般場次
            event = db.session.get(Event, eid)
            if event:
                db.session.delete(event)

            # 2. 安全刪除歷史場次
            archive = db.session.get(Archive, eid)
            if archive:
                db.session.delete(archive)

            # 3. 清空相關的投票與報名紀錄
            Vote.query.filter_by(event_id=eid).delete()
            Registration.query.filter_by(event_id=eid).delete()

            db.session.commit()
            return jsonify({"status": "success"})

        else:
            return jsonify({"status": "error", "message": "未知的操作類型"}), 400

    except InputError as e:
        db.session.rollback()
        return jsonify({"status": "fail", "message": str(e)}), 400
    except Exception:
        # 詳細錯誤只寫進伺服器 log（journalctl -u volleyball），不回傳給使用者
        db.session.rollback()
        app.logger.exception("POST /api 失敗 (type=%s)", action_type)
        return jsonify({"status": "error", "message": "系統發生錯誤，請稍後再試"}), 500
