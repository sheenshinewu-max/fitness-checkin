from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import date, timedelta
import uuid
import calendar as cal_module
import functools
import random

CHECKIN_PRAISE = [
    "卷王在此 👑", "肌肉猛男已就位 💪", "今日份卷已完成 ✅",
    "已成为别人的噩梦 😈", "脂肪见ta就哭 😭", "健身房扛把子 🏋️",
    "人形健身器材 🤖", "今天也是超级英雄 🦸", "连呼吸都在燃脂 🔥",
    "汗水已洒，灵魂升华 ✨", "努力的样子真的很帅 😎", "别人在堕落，ta在逆袭 📈",
]

NOT_CHECKIN_TAUNT = [
    "还在睡？ 😴", "沙发陷进去了吧 🛋️", "你的同伴已抛弃你 👋",
    "脂肪在开派对 🎉", "被窝叫你回去了？ 🛏️", "今天也要鸽？ 🐦",
    "薯片和你说谢谢 🙏", "借口想好了吗 🤔", "明天？明天也这样说的 😑",
    "正在进行脂肪积累 📦", "躺平选手出道 🌊", "连吃饭都有力气，练个球 🍔",
    "你的腹肌在哪？还没长出来 🫃", "手机重量算训练吗？ 📱",
]

URGENCY_BANNER = [
    "🚨 {name} 已经在撸铁了，你还在干嘛！",
    "💀 {name} 的腹肌正在成型，你的还是棉花！",
    "⚡ 警报：{name} 已开始今日卷，你还坐着？！",
    "😤 {name} 都动了，你的脂肪在偷笑呢！",
    "🔔 {name} 打完卡了，你还不起来？脂肪谢谢你！",
    "👀 {name} 已抢先一步，你的借口准备好了吗？",
]

RANK_FIRST = [
    "卷王之王 👑", "健身房的主人 🏋️", "人类的天花板 🚀",
    "本月最能卷的生物 🐉", "地球最强选手 💥", "卷神本神，无可替代 🔱",
    "脂肪克星头号玩家 🎮", "别人的噩梦，自己的传说 😈",
]

RANK_LAST = [
    "最需要努力的人 🌱", "咸鱼的理想型 🐟", "脂肪最忠实的伙伴 🤝",
    "沙发最爱的人 🛋️", "潜力无限（就是没发挥）✨", "明日之星（明天再说）⭐",
    "最有进步空间的选手 📈", "本月最佳摆烂奖得主 🏆",
]

app = Flask(__name__)
app.secret_key = 'fitness-checkin-secret-2026'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def get_db():
    db = sqlite3.connect('fitness.db')
    db.row_factory = sqlite3.Row
    return db


TAGS = ['有氧', '无氧', '练背', '练臀', '练臂', '练核心', '练腿', '练斜方肌']


def init_db():
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS checkins (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            date     TEXT NOT NULL,
            note     TEXT,
            image_path TEXT,
            tags     TEXT DEFAULT "",
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, date)
        );
    ''')
    db.commit()
    # migrate existing db
    try:
        db.execute('ALTER TABLE checkins ADD COLUMN tags TEXT DEFAULT ""')
        db.commit()
    except Exception:
        pass
    db.close()


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_streak(user_id):
    db = get_db()
    rows = db.execute(
        'SELECT date FROM checkins WHERE user_id = ? ORDER BY date DESC',
        (user_id,)
    ).fetchall()
    db.close()
    if not rows:
        return 0
    dates = {r['date'] for r in rows}
    today = date.today()
    check = today if today.isoformat() in dates else today - timedelta(days=1)
    streak = 0
    while check.isoformat() in dates:
        streak += 1
        check -= timedelta(days=1)
    return streak


@app.route('/')
@login_required
def index():
    db = get_db()
    today = date.today().isoformat()
    my_checkin = db.execute(
        'SELECT * FROM checkins WHERE user_id = ? AND date = ?',
        (session['user_id'], today)
    ).fetchone()
    all_users = db.execute('SELECT id, username FROM users ORDER BY username').fetchall()
    today_checkins = db.execute(
        '''SELECT c.*, u.username FROM checkins c
           JOIN users u ON c.user_id = u.id
           WHERE c.date = ? ORDER BY c.created_at ASC''',
        (today,)
    ).fetchall()
    checked_ids = {r['user_id'] for r in today_checkins}
    not_checked = [u for u in all_users if u['id'] not in checked_ids]
    streak = get_streak(session['user_id'])

    # 本月排行榜
    month_prefix = date.today().strftime('%Y-%m-')
    leaderboard_rows = db.execute(
        '''SELECT u.id, u.username, COUNT(c.id) as cnt
           FROM users u
           LEFT JOIN checkins c ON u.id = c.user_id AND c.date LIKE ?
           GROUP BY u.id, u.username
           ORDER BY cnt DESC, u.username ASC''',
        (month_prefix + '%',)
    ).fetchall()
    db.close()

    leaderboard = [dict(r) for r in leaderboard_rows]
    if leaderboard:
        leaderboard[0]['caption'] = random.choice(RANK_FIRST)
        if len(leaderboard) > 1:
            leaderboard[-1]['caption'] = random.choice(RANK_LAST)

    # 随机文案
    praised = [dict(c) | {'praise': random.choice(CHECKIN_PRAISE)} for c in today_checkins]
    taunted = [dict(u) | {'taunt': random.choice(NOT_CHECKIN_TAUNT)} for u in not_checked]

    # 紧迫感横幅：当前用户未打卡但已有人打卡
    urgency = None
    if not my_checkin and today_checkins:
        first_name = today_checkins[0]['username']
        urgency = random.choice(URGENCY_BANNER).format(name=first_name)

    return render_template('index.html',
                           my_checkin=my_checkin,
                           today_checkins=praised,
                           not_checked=taunted,
                           streak=streak,
                           today=today,
                           all_tags=TAGS,
                           urgency=urgency,
                           leaderboard=leaderboard)


@app.route('/checkin', methods=['POST'])
@login_required
def checkin():
    today = date.today().isoformat()
    note = request.form.get('note', '').strip()
    selected_tags = [t for t in request.form.getlist('tags') if t in TAGS]
    tags = ','.join(selected_tags)
    image_path = None
    file = request.files.get('image')
    if file and file.filename and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        image_path = f"uploads/{filename}"
    db = get_db()
    try:
        db.execute(
            'INSERT INTO checkins (user_id, date, note, image_path, tags) VALUES (?, ?, ?, ?, ?)',
            (session['user_id'], today, note, image_path, tags)
        )
        db.commit()
        flash('打卡成功！继续保持 💪', 'success')
    except sqlite3.IntegrityError:
        flash('今天已经打过卡了', 'warning')
    finally:
        db.close()
    return redirect(url_for('index'))


@app.route('/calendar')
@login_required
def calendar_view():
    year = request.args.get('year', date.today().year, type=int)
    month = request.args.get('month', date.today().month, type=int)
    if month < 1:
        month, year = 12, year - 1
    elif month > 12:
        month, year = 1, year + 1
    db = get_db()
    rows = db.execute(
        "SELECT date, tags FROM checkins WHERE user_id = ? AND date LIKE ?",
        (session['user_id'], f'{year}-{month:02d}-%')
    ).fetchall()
    db.close()
    checked_days = {int(r['date'].split('-')[2]) for r in rows}
    day_tags = {int(r['date'].split('-')[2]): [t for t in (r['tags'] or '').split(',') if t]
                for r in rows}
    month_matrix = cal_module.monthcalendar(year, month)
    streak = get_streak(session['user_id'])
    total_db = get_db()
    total = total_db.execute(
        'SELECT COUNT(*) as cnt FROM checkins WHERE user_id = ?',
        (session['user_id'],)
    ).fetchone()['cnt']
    total_db.close()
    month_name = cal_module.month_name[month]
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    return render_template('calendar.html',
                           year=year, month=month, month_name=month_name,
                           month_matrix=month_matrix,
                           checked_days=checked_days,
                           day_tags=day_tags,
                           streak=streak, total=total,
                           today=date.today(),
                           prev_month=prev_month, prev_year=prev_year,
                           next_month=next_month, next_year=next_year)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        if not username or not password:
            flash('请填写用户名和密码', 'error')
            return render_template('register.html')
        db = get_db()
        try:
            db.execute(
                'INSERT INTO users (username, password) VALUES (?, ?)',
                (username, generate_password_hash(password, method='pbkdf2:sha256'))
            )
            db.commit()
            flash('注册成功，请登录', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('用户名已存在', 'error')
        finally:
            db.close()
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        db.close()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('index'))
        flash('用户名或密码错误', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


os.makedirs('static/uploads', exist_ok=True)
init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
