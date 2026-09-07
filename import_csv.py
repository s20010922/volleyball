import csv
from datetime import datetime
from app import app, db, Config, Event, Registration, Vote, Archive

def parse_date(date_str):
    """嘗試解析多種可能的日期格式"""
    if not date_str:
        return None
    date_formats = ["%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M", "%Y/%m/%d"]
    for fmt in date_formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    print(f"警告：無法解析日期格式 '{date_str}'，改用當下時間")
    return datetime.utcnow()

def get_key(row, possible_keys):
    """自動配對 CSV 欄位名稱，無視大小寫與隱藏字元"""
    # 將 CSV 讀取到的真實欄位全部轉小寫並去除空白
    row_keys = {k.strip().lower(): k for k in row.keys() if k}
    for pk in possible_keys:
        if pk.lower() in row_keys:
            return row[row_keys[pk.lower()]]
    return ""

def import_data():
    with app.app_context():
        # 1. Config (採用 utf-8-sig 去除 BOM)
        try:
            with open('Config.csv', 'r', encoding='utf-8-sig') as f:
                reader = csv.reader(f)
                announcement_text = ""
                for row in reader:
                    if row: announcement_text += row[0] + "\n"
                
                config = Config.query.first()
                if not config:
                    db.session.add(Config(announcement=announcement_text.strip()))
                else:
                    config.announcement = announcement_text.strip()
                print("✅ Config 匯入成功")
        except FileNotFoundError:
            pass

        # 2. Events
        try:
            with open('Events.csv', 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    eid = get_key(row, ['event_id', 'id'])
                    if not Event.query.get(eid):
                        max_cap = get_key(row, ['max', 'max_capacity'])
                        db.session.add(Event(
                            id=eid,
                            date=parse_date(get_key(row, ['date'])),
                            title=get_key(row, ['title']),
                            max_capacity=int(max_cap) if str(max_cap).isdigit() else 0,
                            desc=get_key(row, ['desc', 'description']),
                            status=get_key(row, ['status']),
                            options=get_key(row, ['option', 'options'])
                        ))
                print("✅ Events 匯入成功")
        except FileNotFoundError:
            pass

        # 3. Archive
        try:
            with open('Archive.csv', 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    eid = get_key(row, ['event_id', 'id'])
                    if not Archive.query.get(eid):
                        max_cap = get_key(row, ['max', 'max_capacity'])
                        db.session.add(Archive(
                            id=eid,
                            date=parse_date(get_key(row, ['date'])),
                            title=get_key(row, ['title']),
                            max_capacity=int(max_cap) if str(max_cap).isdigit() else 0,
                            desc=get_key(row, ['desc', 'description']),
                            status=get_key(row, ['status']),
                            members=get_key(row, ['members', 'options', 'option'])
                        ))
                print("✅ Archive 匯入成功")
        except FileNotFoundError:
            pass

        # 4. Registrations
        try:
            with open('Registrations.csv', 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    eid = get_key(row, ['event_id', 'id'])
                    balls = get_key(row, ['balls'])
                    db.session.add(Registration(
                        event_id=eid,
                        name=get_key(row, ['name']),
                        balls=int(balls) if str(balls).isdigit() else 0,
                        note=get_key(row, ['note']),
                        timestamp=parse_date(get_key(row, ['timestamp']))
                    ))
                print("✅ Registrations 匯入成功")
        except FileNotFoundError:
            pass

        # 5. Vote
        try:
            with open('Vote.csv', 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    eid = get_key(row, ['event_id', 'id'])
                    db.session.add(Vote(
                        event_id=eid,
                        name=get_key(row, ['name']),
                        choice=get_key(row, ['choice']),
                        timestamp=parse_date(get_key(row, ['timestamp']))
                    ))
                print("✅ Vote 匯入成功")
        except FileNotFoundError:
            pass

        db.session.commit()
        print("🎉 所有資料庫匯入操作完成！")

if __name__ == "__main__":
    import_data()
