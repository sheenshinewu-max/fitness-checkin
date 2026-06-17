from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import psycopg2
import psycopg2.extras
import os
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import date, timedelta
import uuid
import calendar as cal_module
import functools
import random
import oss2
from PIL import Image
import io

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
app.secret_key = os.environ.get('SECRET_KEY', 'fitness-checkin-secret-2026')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

DATABASE_URL = os.environ.get('DATABASE_URL')

OSS_ACCESS_KEY_ID     = os.environ.get('OSS_ACCESS_KEY_ID')
OSS_ACCESS_KEY_SECRET = os.environ.get('OSS_ACCESS_KEY_SECRET')
OSS_BUCKET_NAME       = os.environ.get('OSS_BUCKET_NAME')
OSS_ENDPOINT          = os.environ.get('OSS_ENDPOINT')

TAGS = ['有氧', '无氧', '练背', '练臀', '练臂', '练核心', '练腿', '练斜方肌']
INVITE_CODES = ["我要是不坚持我就反弹", "boom666"]
ADMIN_USERNAME = '五花'


# ── DB helpers ──────────────────────────────────────────────────────────────

def get_db():
    return psycopg2.connect(DATABASE_URL)


def query(sql, params=(), one=False):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params)
    result = cur.fetchone() if one else cur.fetchall()
    cur.close()
    conn.close()
    return result


def execute(sql, params=()):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    cur.close()
    conn.close()


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id         SERIAL PRIMARY KEY,
            username   TEXT UNIQUE NOT NULL,
            password   TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS checkins (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            date       TEXT NOT NULL,
            note       TEXT,
            image_path TEXT,
            tags       TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, date)
        )
    ''')
    cur.execute("ALTER TABLE checkins ADD COLUMN IF NOT EXISTS tags TEXT DEFAULT ''")
    cur.execute('''
        CREATE TABLE IF NOT EXISTS campaigns (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date   TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()


# ── Campaign helpers ─────────────────────────────────────────────────────────

def get_active_campaign():
    today = date.today().isoformat()
    execute(
        "UPDATE campaigns SET status = 'archived' WHERE status = 'active' AND end_date < %s",
        (today,)
    )
    return query("SELECT * FROM campaigns WHERE status = 'active' LIMIT 1", one=True)


def campaign_date_range(campaign):
    """Return (start, end_capped) strings for a campaign, end capped at today."""
    today = date.today().isoformat()
    return campaign['start_date'], min(campaign['end_date'], today)


# ── Auth decorators ──────────────────────────────────────────────────────────

def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if session.get('username') != ADMIN_USERNAME:
            flash('没有权限，你是谁？ 🤨', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return wrapper


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ── Streak ───────────────────────────────────────────────────────────────────

def get_streak(user_id, start_date=None, end_date=None):
    today = date.today()
    if start_date and end_date:
        cap = min(end_date, today.isoformat())
        rows = query(
            'SELECT date FROM checkins WHERE user_id = %s AND date >= %s AND date <= %s',
            (user_id, start_date, cap)
        )
    else:
        rows = query('SELECT date FROM checkins WHERE user_id = %s', (user_id,))
    if not rows:
        return 0
    dates = {r['date'] for r in rows}
    cap_date = date.fromisoformat(min(end_date, today.isoformat())) if end_date else today
    min_date = date.fromisoformat(start_date) if start_date else None
    check = cap_date if cap_date.isoformat() in dates else cap_date - timedelta(days=1)
    streak = 0
    while check.isoformat() in dates:
        if min_date and check < min_date:
            break
        streak += 1
        check -= timedelta(days=1)
    return streak


# ── Image processing ─────────────────────────────────────────────────────────

def compress_image(file_stream, max_px=1920, quality=85):
    img = Image.open(file_stream)
    if img.mode not in ('RGB', 'RGBA'):
        img = img.convert('RGB')
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    fmt = 'JPEG' if img.mode == 'RGB' else 'PNG'
    img.save(buf, format=fmt, quality=quality, optimize=True)
    buf.seek(0)
    return buf, fmt.lower()


# ── OSS ──────────────────────────────────────────────────────────────────────

def upload_to_oss(file_stream, filename):
    auth   = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, f'https://{OSS_ENDPOINT}', OSS_BUCKET_NAME)
    object_key = f'fitness-checkin/{filename}'
    bucket.put_object(object_key, file_stream,
                      headers={'x-oss-object-acl': 'public-read'})
    return f'https://{OSS_BUCKET_NAME}.{OSS_ENDPOINT}/{object_key}'


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    today = date.today().isoformat()
    campaign = get_active_campaign()

    my_checkin = query(
        'SELECT * FROM checkins WHERE user_id = %s AND date = %s',
        (session['user_id'], today), one=True
    )
    all_users    = query('SELECT id, username FROM users ORDER BY username')
    today_checkins = query(
        '''SELECT c.*, u.username FROM checkins c
           JOIN users u ON c.user_id = u.id
           WHERE c.date = %s ORDER BY c.created_at ASC''',
        (today,)
    )
    checked_ids = {r['user_id'] for r in today_checkins}
    not_checked = [u for u in all_users if u['id'] not in checked_ids]

    # Streak & leaderboard: scoped to campaign or all-time
    if campaign:
        s, e = campaign_date_range(campaign)
        streak = get_streak(session['user_id'], s, e)
        leaderboard_rows = query(
            '''SELECT u.id, u.username, COUNT(c.id) as cnt
               FROM users u
               LEFT JOIN checkins c ON u.id = c.user_id
                   AND c.date >= %s AND c.date <= %s
               GROUP BY u.id, u.username
               ORDER BY cnt DESC, u.username ASC''',
            (s, e)
        )
        # remaining days
        remaining = (date.fromisoformat(campaign['end_date']) - date.today()).days
        total_days = (date.fromisoformat(campaign['end_date']) -
                      date.fromisoformat(campaign['start_date'])).days + 1
        elapsed    = (date.today() - date.fromisoformat(campaign['start_date'])).days + 1
        campaign_info = dict(campaign) | {
            'remaining': max(0, remaining),
            'total_days': total_days,
            'elapsed': min(elapsed, total_days),
        }
    else:
        streak = get_streak(session['user_id'])
        month_prefix = date.today().strftime('%Y-%m-')
        leaderboard_rows = query(
            '''SELECT u.id, u.username, COUNT(c.id) as cnt
               FROM users u
               LEFT JOIN checkins c ON u.id = c.user_id AND c.date LIKE %s
               GROUP BY u.id, u.username
               ORDER BY cnt DESC, u.username ASC''',
            (month_prefix + '%',)
        )
        campaign_info = None

    leaderboard = [dict(r) for r in leaderboard_rows]
    if leaderboard:
        leaderboard[0]['caption'] = random.choice(RANK_FIRST)
        if len(leaderboard) > 1:
            leaderboard[-1]['caption'] = random.choice(RANK_LAST)

    praised = [dict(c) | {'praise': random.choice(CHECKIN_PRAISE)} for c in today_checkins]
    taunted = [dict(u) | {'taunt': random.choice(NOT_CHECKIN_TAUNT)} for u in not_checked]

    urgency = None
    if not my_checkin and today_checkins:
        urgency = random.choice(URGENCY_BANNER).format(name=today_checkins[0]['username'])

    return render_template('index.html',
                           my_checkin=my_checkin,
                           today_checkins=praised,
                           not_checked=taunted,
                           streak=streak,
                           today=today,
                           all_tags=TAGS,
                           urgency=urgency,
                           leaderboard=leaderboard,
                           campaign=campaign_info)


@app.route('/checkin', methods=['POST'])
@login_required
def checkin():
    today = date.today().isoformat()
    note  = request.form.get('note', '').strip()
    selected_tags = [t for t in request.form.getlist('tags') if t in TAGS]
    tags  = ','.join(selected_tags)
    image_path = None
    file = request.files.get('image')
    if file and file.filename and allowed_file(file.filename):
        buf, fmt = compress_image(file.stream)
        image_path = upload_to_oss(buf, f"{uuid.uuid4().hex}.{fmt}")
    try:
        execute(
            'INSERT INTO checkins (user_id, date, note, image_path, tags) VALUES (%s, %s, %s, %s, %s)',
            (session['user_id'], today, note, image_path, tags)
        )
        flash('打卡成功！继续保持 💪', 'success')
    except psycopg2.IntegrityError:
        flash('今天已经打过卡了', 'warning')
    return redirect(url_for('index'))


@app.route('/calendar')
@login_required
def calendar_view():
    year  = request.args.get('year',  date.today().year,  type=int)
    month = request.args.get('month', date.today().month, type=int)
    if month < 1:
        month, year = 12, year - 1
    elif month > 12:
        month, year = 1, year + 1
    rows = query(
        "SELECT date, tags FROM checkins WHERE user_id = %s AND date LIKE %s",
        (session['user_id'], f'{year}-{month:02d}-%')
    )
    checked_days = {int(r['date'].split('-')[2]) for r in rows}
    day_tags = {int(r['date'].split('-')[2]): [t for t in (r['tags'] or '').split(',') if t]
                for r in rows}
    month_matrix = cal_module.monthcalendar(year, month)
    streak    = get_streak(session['user_id'])
    total_row = query('SELECT COUNT(*) as cnt FROM checkins WHERE user_id = %s',
                      (session['user_id'],), one=True)
    total = total_row['cnt'] if total_row else 0
    month_name = cal_module.month_name[month]
    prev_month = month - 1 if month > 1 else 12
    prev_year  = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year  = year if month < 12 else year + 1
    return render_template('calendar.html',
                           year=year, month=month, month_name=month_name,
                           month_matrix=month_matrix,
                           checked_days=checked_days, day_tags=day_tags,
                           streak=streak, total=total, today=date.today(),
                           prev_month=prev_month, prev_year=prev_year,
                           next_month=next_month, next_year=next_year)


@app.route('/admin', methods=['GET', 'POST'])
@login_required
@admin_required
def admin():
    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        duration = request.form.get('duration', '').strip()
        if not name or not duration.isdigit() or int(duration) < 1:
            flash('请填写活动名称和有效天数', 'error')
        else:
            active = query("SELECT id FROM campaigns WHERE status = 'active' LIMIT 1", one=True)
            if active:
                flash('当前已有进行中的活动，请先结束后再创建', 'error')
            else:
                start = date.today()
                end   = start + timedelta(days=int(duration) - 1)
                execute(
                    "INSERT INTO campaigns (name, start_date, end_date, status) VALUES (%s, %s, %s, 'active')",
                    (name, start.isoformat(), end.isoformat())
                )
                flash(f'活动「{name}」已创建，共 {duration} 天，加油！🔥', 'success')
        return redirect(url_for('admin'))

    campaigns = query("SELECT * FROM campaigns ORDER BY created_at DESC")
    return render_template('admin.html', campaigns=campaigns)


@app.route('/admin/campaign/<int:cid>/archive', methods=['POST'])
@login_required
@admin_required
def archive_campaign(cid):
    execute("UPDATE campaigns SET status = 'archived' WHERE id = %s", (cid,))
    flash('活动已手动归档', 'success')
    return redirect(url_for('admin'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        invite   = request.form.get('invite_code', '').strip()
        if not username or not password:
            flash('请填写用户名和密码', 'error')
            return render_template('register.html')
        if invite not in INVITE_CODES:
            flash('邀请码错误，你是怎么知道这里的？ 👀', 'error')
            return render_template('register.html')
        try:
            execute(
                'INSERT INTO users (username, password) VALUES (%s, %s)',
                (username, generate_password_hash(password, method='pbkdf2:sha256'))
            )
            flash('注册成功，请登录', 'success')
            return redirect(url_for('login'))
        except psycopg2.IntegrityError:
            flash('用户名已存在', 'error')
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        user = query('SELECT * FROM users WHERE username = %s', (username,), one=True)
        if user and check_password_hash(user['password'], password):
            session.permanent = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('index'))
        flash('用户名或密码错误', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
