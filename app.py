import os
from datetime import datetime
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///volleyball.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

admin_plain_pwd = os.getenv('ADMIN_PWD', 'fallback_random_string_xyz')
ADMIN_PWD_HASH = generate_password_hash(admin_plain_pwd)

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
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    balls = db.Column(db.Integer, default=0)
    note = db.Column(db.Text, default="")
    timestamp = db.Column(db.DateTime, default=datetime.now)

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    choice = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)

class Archive(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    date = db.Column(db.DateTime, nullable=False)
    title = db.Column(db.String(100), nullable=False)
    max_capacity = db.Column(db.Integer, default=0)
    desc = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="archive")
    members = db.Column(db.Text, default="")

# ==========================================
# 輔助函式
# ==========================================
def verify_admin(password):
    if not password: return False
    return check_password_hash(ADMIN_PWD_HASH, str(password))

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

def auto_process_finished_votes():
    now = datetime.now()
    events = Event.query.filter(Event.status == 'vote', Event.date <= now).all()
    for event in events:
        votes = Vote.query.filter_by(event_id=event.id).all()
        if votes:
            stats = {}
            for v in votes:
                stats[v.choice] = stats.get(v.choice, 0) + 1
            # 找出最高票
            winner_date_str = sorted(stats.keys(), key=lambda k: stats[k], reverse=True)[0]

            try:
                final_date = datetime.strptime(winner_date_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                final_date = event.date

            event.date = final_date
            event.status = 'active'
            Vote.query.filter_by(event_id=event.id).delete()
        else:
            event.status = 'archive'
    db.session.commit()

def auto_archive_events():
    now = now = datetime.now()
    events = Event.query.filter(Event.status.in_(['active', 'vote']), Event.date < now).all()
    for event in events:
        regs = Registration.query.filter_by(event_id=event.id).all()
        members_str = ", ".join([r.name for r in regs])

        archive = Archive(
            id=event.id,
            date=event.date,
            title=event.title,
            max_capacity=event.max_capacity,
            desc=event.desc,
            status='archive',
            members=members_str
        )
        db.session.add(archive)

        Registration.query.filter_by(event_id=event.id).delete()
        db.session.delete(event)
    db.session.commit()

# ==========================================
# API 路由
# ==========================================
@app.route('/api', methods=['GET'])
def get_data():
    try:
        # 自動執行維護任務
        auto_process_finished_votes()
        auto_archive_events()

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
    except Exception as e:
        return jsonify({"status": "error", "message": f"讀取失敗：{str(e)}"})

@app.route('/api', methods=['POST'])
def handle_post():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "JSON 解析錯誤"}), 400

        action_type = data.get('type')
        admin_actions = ['verify', 'updateAnnouncement', 'createEvent', 'deleteEvent', 'convertToActive', 'archiveEvent']

        if action_type in admin_actions:
            if not verify_admin(data.get('adminPassword')):
                return jsonify({"status": "fail", "message": "管理密碼錯誤"}), 403

        if action_type == 'verify':
            return jsonify({"status": "success"})

        elif action_type == 'updateAnnouncement':
            config = Config.query.first()
            config.announcement = data.get('content', '')
            db.session.commit()
            return jsonify({"status": "success"})

        elif action_type == 'createEvent':
            eid = data.get('event_id') or data.get('id') or f"E{int(datetime.now().timestamp()*1000)}"
            max_cap = int(data.get('max') or data.get('maxCount') or 0)

            try:
                event_date = datetime.strptime(data.get('date', ''), "%Y-%m-%dT%H:%M")
            except:
                event_date = datetime.now()

            new_event = Event(
                id=eid,
                date=event_date,
                title=data.get('title', ''),
                max_capacity=max_cap,
                desc=data.get('desc', ''),
                status=data.get('status', 'active'),
                options=data.get('options', '')
            )
            db.session.add(new_event)
            db.session.commit()
            return jsonify({"status": "success", "id": eid})

        elif action_type == 'vote':
            eid = str(data.get('event_id') or data.get('eventId')).strip()
            name = str(data.get('name', '')).strip()
            choice = str(data.get('choice', '')).strip()

            event = db.session.get(Event, eid)
            if not event:
                return jsonify({"status": "fail", "message": "找不到場次"})

            if Vote.query.filter_by(event_id=eid, name=name).first():
                return jsonify({"status": "fail", "message": "您已投過票囉！"})

            new_vote = Vote(event_id=eid, name=name, choice=choice)
            db.session.add(new_vote)
            db.session.commit()
            return jsonify({"status": "success"})

        elif action_type == 'register':
            eid = str(data.get('event_id') or data.get('eventId')).strip()
            name = str(data.get('name', '')).strip()

            event = db.session.get(Event, eid)
            if not event:
                return jsonify({"status": "fail", "message": "找不到場次"})

            if Registration.query.filter_by(event_id=eid, name=name).first():
                return jsonify({"status": "fail", "message": "您已經報名過此場次囉！"})

            if event.status.lower() == 'active':
                current_count = Registration.query.filter_by(event_id=eid).count()
                if current_count >= event.max_capacity:
                    return jsonify({"status": "fail", "message": "名額已滿！"})

            new_reg = Registration(
                event_id=eid,
                name=name,
                balls=int(data.get('balls', 0)),
                note=data.get('note', '')
            )
            db.session.add(new_reg)
            db.session.commit()
            return jsonify({"status": "success"})

        elif action_type == 'cancelRegistration':
            eid = str(data.get('event_id') or data.get('eventId')).strip()
            name = str(data.get('name', '')).strip()

            reg = Registration.query.filter_by(event_id=eid, name=name).first()
            if reg:
                db.session.delete(reg)
                db.session.commit()
                return jsonify({"status": "success"})
            return jsonify({"status": "fail", "message": "找不到該姓名的報名紀錄，請檢查輸入是否正確。"})

        elif action_type == 'cancelVote':
            eid = str(data.get('event_id') or data.get('eventId')).strip()
            name = str(data.get('name', '')).strip()

            vote = Vote.query.filter_by(event_id=eid, name=name).first()
            if vote:
                db.session.delete(vote)
                db.session.commit()
                return jsonify({"status": "success"})
            return jsonify({"status": "fail", "message": "找不到您的投票紀錄，請檢查姓名是否正確。"})

        elif action_type == 'archiveEvent':
            eid = str(data.get('event_id') or data.get('eventId')).strip()
            event = db.session.get(Event, eid)
            if event:
                event.status = 'archive'
                db.session.commit()
                return jsonify({"status": "success"})
            return jsonify({"status": "fail", "message": "找不到場次"})

        elif action_type == 'convertToActive':
            eid = str(data.get('event_id') or data.get('eventId')).strip()
            event = db.session.get(Event, eid)
            if event:
                new_date = data.get('newDate')
                if new_date:
                    try:
                        event.date = datetime.strptime(new_date, "%Y-%m-%dT%H:%M")
                    except:
                        pass
                if data.get('newTitle'):
                    event.title = data.get('newTitle')
                event.status = 'active'
                db.session.commit()
                return jsonify({"status": "success"})
            return jsonify({"status": "fail", "message": "找不到場次"})

        elif action_type == 'deleteEvent':
            eid = str(data.get('event_id') or data.get('eventId')).strip()
            
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
            return jsonify({"status": "error", "message": "未知的操作類型"})

    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": f"執行錯誤：{str(e)}"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
