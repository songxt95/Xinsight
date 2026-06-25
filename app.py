import io
import csv
import json
import time
import os
import ssl
import sys
import secrets
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, Response, request, render_template, redirect, url_for, jsonify, send_file, session
from playwright.sync_api import sync_playwright

from data_loader import (
    fetch_data, perform_login, fetch_class_list,
    fetch_class_curriculum, fetch_student_overall_stats, fetch_student_detailed_history_v2,
    fetch_class_detail, fetch_lesson_question_snapshot
)

# 解析 .env 文件的函数
def load_env_natively(env_path='.env'):
    if not os.path.exists(env_path):
        return
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # 忽略空行和以 # 开头的注释行
            if not line or line.startswith('#'):
                continue
            # 按第一个等号分割键值对
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                # 去除两端可能包含的引号 (单引号或双引号)
                if value.startswith(("'", '"')) and value.endswith(("'", '"')):
                    value = value[1:-1]
                # 写入系统环境变量
                os.environ[key] = value

# 在应用启动前调用此函数加载配置
load_env_natively()

app = Flask(__name__)

# [安全配置]
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
# 设置 Session 有效期为 30 天，模拟“自动登录”体验，但数据存在客户端
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)


def resolve_export_browser_executable():
    """优先使用环境变量，其次回退到系统已安装浏览器路径（跨平台）。"""
    candidates = []

    env_path = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE', '').strip()
    if env_path:
        candidates.append(env_path)

    if sys.platform == 'darwin':
        # macOS
        candidates.extend([
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
            '/Applications/Chromium.app/Contents/MacOS/Chromium',
        ])
    elif sys.platform.startswith('linux'):
        # Linux
        candidates.extend([
            '/usr/bin/google-chrome',
            '/usr/bin/google-chrome-stable',
            '/usr/bin/chromium',
            '/usr/bin/chromium-browser',
            '/usr/bin/microsoft-edge',
        ])
    else:
        # Windows
        candidates.extend([
            'C:/Program Files/Google/Chrome/Application/chrome.exe',
            'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
            'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
        ])

    for path in candidates:
        if path and os.path.exists(path):
            return path
    # 都找不到时返回 None，Playwright 会回退到自带 chromium
    return None


@app.route('/', methods=['GET', 'POST'])
def index():
    # [关键修改] 从 Session 获取当前用户的 Token，实现多人隔离
    token = session.get('token')
    teacherType = session.get('teacherType', '0')
    username = session.get('username', '')

    # 辅助参数 (单讲看板用)
    class_id = request.values.get('classId')
    cuc_num_str = request.values.get('cucNum')
    class_name = request.values.get('className', '').strip()
    class_year = request.values.get('classYear', '').strip()

    # === 场景 A: 显示单讲看板 ===
    if token and class_id and cuc_num_str:
        if not class_year or not class_name:
            detail = fetch_class_detail(token, class_id)
            if detail:
                if not class_year: class_year = str(detail.get('year', datetime.now().year))
                if not class_name: class_name = detail.get('name', '班级详情')
        if not class_name: class_name = "班级详情"
        if not class_year: class_year = str(datetime.now().year)

        cuc_num = int(cuc_num_str)

        lessons = fetch_class_curriculum(token, class_id)
        current_lesson_name = f"第 {cuc_num} 讲"
        max_lessons = 1

        if lessons:
            max_lessons = len(lessons)
            for l in lessons:
                if l['num'] == cuc_num:
                    current_lesson_name = f"第 {cuc_num} 讲 {l['name']}"
                    break

        headers, rows, practice_names, orders, categories, stats = fetch_data(class_id, token, cuc_num, teacherType)

        if not headers:
             session.clear() # Token失效，清理Session
             return render_template('login.html', error=stats.get('error'), default_user=username)

        download_url = url_for('download_csv', classId=class_id, cucNum=cuc_num, className=class_name)

        return render_template('index.html', show_dashboard=True,
                               headers=headers,
                               rows=rows,
                               practice_names=practice_names,
                               practice_orders=orders,
                               practice_categories=categories,
                               stats=stats,
                               current_cuc_num=cuc_num,
                               current_class_id=class_id,
                               current_class_name=class_name,
                               current_class_year=class_year,
                               username=username,
                               password="***", # 密码不再透传到前端
                               teacherType=teacherType,
                               current_lesson_name=current_lesson_name,
                               max_lessons=max_lessons,
                               download_url=download_url,
                               rows_json=json.dumps(rows),
                               cols_json=json.dumps(practice_names),
                               orders_json=json.dumps(orders),
                               cats_json=json.dumps(categories)
                               )

    # === 场景 B: 自动登录 (Session中有Token) ===
    # 处理刷新逻辑：如果 Form 提交了 range，使用 Form 的；否则默认 180
    search_range = int(request.form.get('range', 180)) if request.method == 'POST' and 'range' in request.form else 180

    if token:
        class_groups, err = fetch_class_list(token, search_range, teacherType)
        if not err:
            return render_template('index.html', show_class_selector=True, class_groups=class_groups,
                                   username=username, search_range=search_range, teacherType=teacherType)
        else:
            # Token 可能过期
            session.clear()
            return render_template('login.html', error="登录已过期，请重新登录", default_user=username)

    # === 场景 C: 登录表单提交 ===
    if request.method == 'POST' and 'username' in request.form:
        username = request.form.get('username')
        password = request.form.get('password')
        teacherType = request.form.get('teacherType', "0")

        login_log_tag = f"[LoginAttempt:{username}][IP:{request.remote_addr}]"
        logging.info(f"{login_log_tag} >>> 收到登录请求...")

        start_time = time.time()
        token, error = perform_login(username, password, teacherType)
        login_duration = time.time() - start_time

        if not token:
            # [安全] 失败强制延迟 1 秒
            logging.info(f"{login_log_tag} 登录失败: {error} (耗时: {login_duration:.2f}s) -> 执行延迟惩罚")
            time.sleep(1)
            return render_template('login.html', error=error, default_user=username)
        else:
            # [关键] 登录成功，写入 Session
            logging.info(f"{login_log_tag} 验证成功 (耗时: {login_duration:.2f}s)")
            session.permanent = True
            session['token'] = token
            session['username'] = username
            session['teacherType'] = teacherType

            # 立即获取列表
            start_fetch = time.time()
            class_groups, err = fetch_class_list(token, search_range, teacherType)

            fetch_duration = time.time() - start_fetch
            logging.info(f"{login_log_tag} 获取班级列表成功 (耗时: {fetch_duration:.2f}s)")

            if err:
                logging.info(f"{login_log_tag} 获取班级列表失败: {err} (耗时: {fetch_duration:.2f}s)")
                return render_template('login.html', error=err, default_user=username)

            return render_template('index.html', show_class_selector=True, class_groups=class_groups,
                                   username=username, search_range=search_range, teacherType=teacherType)

    # === 默认场景: 显示登录页 ===
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear() # [v119] 退出登录只需清空 Cookie
    return redirect(url_for('index'))

@app.route('/api/get_curriculum')
def get_curriculum():
    token = session.get('token')
    class_id = request.args.get('classId')
    lessons = fetch_class_curriculum(token, class_id)
    return jsonify({'lessons': lessons})


def _flatten_class_groups(class_groups):
    flattened = []
    for group_key in ['open', 'closed', 'self']:
        for cls in class_groups.get(group_key, []):
            start_date = str(cls.get('startDate', '') or '')
            end_date = str(cls.get('endTime', '') or '')
            time_text = str(cls.get('time', '') or '')
            term_name = str(cls.get('termName', '') or '')
            year = str(cls.get('year', '') or '')

            time_label = time_text or '时间未知'

            flattened.append({
                'id': cls.get('id', ''),
                'name': cls.get('name', '未命名班级'),
                'year': year,
                'group': group_key,
                'type': cls.get('type', ''),
                'status': cls.get('status', 0),
                'termName': term_name,
                'startDate': start_date,
                'endDate': end_date,
                'timeText': time_text,
                'timeLabel': time_label
            })
    return flattened


@app.route('/pivot')
def pivot_dashboard():
    token = session.get('token')
    teacherType = session.get('teacherType', '0')
    username = session.get('username', '')

    if not token:
        return redirect(url_for('index'))

    try:
        search_range = int(request.args.get('range', 180))
    except (TypeError, ValueError):
        search_range = 180

    class_groups, err = fetch_class_list(token, search_range, teacherType)
    if err:
        session.clear()
        return render_template('login.html', error='登录已过期，请重新登录', default_user=username)

    selected_ids = [x.strip() for x in request.args.get('classIds', '').split(',') if x.strip()]

    try:
        lesson_start = int(request.args.get('lessonStart', 1))
    except (TypeError, ValueError):
        lesson_start = 1
    try:
        lesson_end = int(request.args.get('lessonEnd', lesson_start))
    except (TypeError, ValueError):
        lesson_end = lesson_start

    lesson_start = max(1, lesson_start)
    lesson_end = max(lesson_start, lesson_end)

    return render_template(
        'pivot_dashboard.html',
        username=username,
        search_range=search_range,
        class_groups=class_groups,
        all_classes_json=json.dumps(_flatten_class_groups(class_groups), ensure_ascii=False),
        selected_class_ids_json=json.dumps(selected_ids, ensure_ascii=False),
        lesson_start=lesson_start,
        lesson_end=lesson_end
    )


@app.route('/api/pivot/accuracy', methods=['POST'])
def pivot_accuracy_api():
    token = session.get('token')
    teacherType = session.get('teacherType', '0')

    if not token:
        return jsonify({'error': '请先登录后再使用透视看板'}), 401

    payload = request.get_json(silent=True) or {}
    raw_classes = payload.get('classes', [])

    selected_classes = []
    seen_ids = set()
    for item in raw_classes:
        class_id = str(item.get('id', '')).strip()
        if not class_id or class_id in seen_ids:
            continue
        selected_classes.append({
            'id': class_id,
            'name': str(item.get('name', '')).strip() or class_id,
            'year': str(item.get('year', '')).strip(),
            'timeText': str(item.get('timeText', '')).strip(),
            'timeLabel': str(item.get('timeLabel', '')).strip()
        })
        seen_ids.add(class_id)

    if not selected_classes:
        return jsonify({'error': '请至少选择一个班级'}), 400

    try:
        lesson_start = int(payload.get('lessonStart', 1))
        lesson_end = int(payload.get('lessonEnd', lesson_start))
    except (TypeError, ValueError):
        return jsonify({'error': '讲次范围必须是数字'}), 400

    lesson_start = max(1, lesson_start)
    lesson_end = max(lesson_start, lesson_end)

    if lesson_end - lesson_start > 30:
        return jsonify({'error': '讲次范围过大，请控制在 30 讲以内'}), 400

    lesson_name_map = {}
    class_lesson_map = {}

    for cls in selected_classes:
        lessons = fetch_class_curriculum(token, cls['id']) or []
        lesson_nums = []
        for lesson in lessons:
            try:
                lesson_num = int(lesson.get('num', 0) or 0)
            except (TypeError, ValueError):
                continue
            if lesson_num < lesson_start or lesson_num > lesson_end:
                continue
            lesson_nums.append(lesson_num)
            if lesson_num not in lesson_name_map:
                lesson_name_map[lesson_num] = lesson.get('name') or f"第{lesson_num}讲"
        class_lesson_map[cls['id']] = sorted(set(lesson_nums))

    lesson_nums_all = sorted({num for nums in class_lesson_map.values() for num in nums})
    if not lesson_nums_all:
        lesson_nums_all = list(range(lesson_start, lesson_end + 1))

    for num in lesson_nums_all:
        lesson_name_map.setdefault(num, f"第{num}讲")

    jobs = []
    for cls in selected_classes:
        available_nums = class_lesson_map.get(cls['id'], [])
        for lesson_num in lesson_nums_all:
            if available_nums and lesson_num not in available_nums:
                continue
            jobs.append((cls, lesson_num))

    if len(jobs) > 240:
        return jsonify({'error': '请求规模过大，请减少班级数或缩小讲次范围'}), 400

    class_result_map = {
        cls['id']: {
            'classId': cls['id'],
            'className': cls['name'],
            'classYear': cls['year'],
            'timeText': cls.get('timeText', ''),
            'timeLabel': cls.get('timeLabel', ''),
            'lessons': {},
            'students': {}
        }
        for cls in selected_classes
    }

    question_union = defaultdict(dict)
    failed_pairs = []

    def fetch_pair(class_info, lesson_num):
        snapshot, err = fetch_lesson_question_snapshot(class_info['id'], token, lesson_num, teacherType)
        return class_info, lesson_num, snapshot, err

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(fetch_pair, cls, lesson_num) for cls, lesson_num in jobs]
        for future in as_completed(futures):
            class_info, lesson_num, snapshot, err = future.result()
            lesson_key = str(lesson_num)

            if err or not snapshot:
                failed_pairs.append({
                    'classId': class_info['id'],
                    'className': class_info['name'],
                    'lessonNum': lesson_num,
                    'reason': (err or {}).get('error', 'unknown') if isinstance(err, dict) else 'unknown'
                })
                continue

            class_entry = class_result_map[class_info['id']]
            summary = snapshot.get('summary', {})

            question_meta_map = {}
            for q in snapshot.get('questions', []):
                uid = q.get('uid')
                if not uid:
                    continue
                sort_key = q.get('sortKey') or [999, 999, uid]
                order_label = f"{sort_key[0]}.{sort_key[1]}" if len(sort_key) >= 2 else ''
                question_meta = {
                    'questionUid': uid,
                    'practiceId': q.get('practiceId') or uid,
                    'practiceName': q.get('practiceName') or '',
                    'displayName': q.get('displayName') or (q.get('practiceId') or uid),
                    'bundleLabel': q.get('bundleLabel') or '',
                    'orderLabel': order_label,
                    'sortKey': sort_key
                }
                question_meta_map[uid] = question_meta
                if uid not in question_union[lesson_key]:
                    question_union[lesson_key][uid] = question_meta

            class_question_map = {}
            for item in snapshot.get('classCells', []):
                uid = item.get('questionUid')
                if not uid:
                    continue
                class_question_map[uid] = {
                    'accuracy': item.get('accuracy'),
                    'completionRate': item.get('completionRate'),
                    'correctCount': item.get('correctCount', 0),
                    'answeredCount': item.get('answeredCount', 0)
                }

            class_entry['lessons'][lesson_key] = {
                'accuracy': summary.get('accuracy'),
                'completionRate': summary.get('completionRate'),
                'correctCount': summary.get('correctCount', 0),
                'answeredCount': summary.get('answeredCount', 0),
                'totalQuestionCount': summary.get('totalQuestionCount', 0),
                'questionCount': summary.get('questionCount', 0),
                'studentCount': summary.get('studentCount', 0),
                'questions': class_question_map
            }

            for stu in snapshot.get('students', []):
                student_name = stu.get('studentName', '未知学员')
                student_id = str(stu.get('studentId', '')).strip()
                student_key = student_id or f"name::{student_name}"

                student_entry = class_entry['students'].setdefault(student_key, {
                    'studentId': student_id,
                    'studentName': student_name,
                    'lessons': {}
                })

                student_question_map = {}
                for cell in stu.get('cells', []):
                    uid = cell.get('questionUid')
                    if not uid:
                        continue
                    student_question_map[uid] = {
                        'accuracy': cell.get('accuracy'),
                        'completionRate': cell.get('completionRate'),
                        'answered': bool(cell.get('answered')),
                        'correct': bool(cell.get('correct')),
                        'raw': cell.get('raw', '-')
                    }

                student_entry['lessons'][lesson_key] = {
                    'accuracy': stu.get('accuracy'),
                    'completionRate': stu.get('completionRate'),
                    'correctCount': stu.get('correctCount', 0),
                    'answeredCount': stu.get('answeredCount', 0),
                    'questions': student_question_map
                }

    classes_output = []
    for cls in selected_classes:
        class_entry = class_result_map[cls['id']]
        students_sorted = sorted(class_entry['students'].values(), key=lambda x: x['studentName'])
        class_entry['students'] = students_sorted
        classes_output.append(class_entry)

    lessons_output = [
        {'num': num, 'key': str(num), 'name': lesson_name_map.get(num, f"第{num}讲")}
        for num in lesson_nums_all
    ]

    question_columns = {}
    for lesson in lessons_output:
        lesson_key = lesson['key']
        columns = list(question_union.get(lesson_key, {}).values())
        columns.sort(key=lambda x: tuple(x.get('sortKey', [999, 999, x.get('practiceId', '')])))
        for item in columns:
            item.pop('sortKey', None)
        question_columns[lesson_key] = columns

    return jsonify({
        'metric': 'completionRate',
        'metrics': {
            'completionRate': {
                'label': '完成率',
                'formula': '答对总题数/总题数'
            },
            'accuracy': {
                'label': '正确率',
                'formula': '答对总题数/已作答总题数，未作答不计入分母'
            }
        },
        'defaultView': {'row': 'class', 'column': 'lesson'},
        'lessons': lessons_output,
        'questionColumns': question_columns,
        'classes': classes_output,
        'meta': {
            'selectedClassCount': len(selected_classes),
            'selectedLessonCount': len(lessons_output),
            'failedPairs': failed_pairs,
            'failedCount': len(failed_pairs),
            'generatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    })

@app.route('/download')
def download_csv():
    token = session.get('token')
    class_id = request.args.get('classId')
    cuc_num = int(request.args.get('cucNum', 1))
    class_name = request.args.get('className', 'class_stats')
    teacherType = session.get('teacherType', '0')

    headers, rows, _, _, _, _ = fetch_data(class_id, token, cuc_num, teacherType)
    if not headers: return "Error fetching data"

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(headers)
    for r in rows:
        row_vals = [r['name'], r['progress_text']] + r['scores']
        cw.writerow(row_vals)

    output = io.BytesIO()
    output.write(si.getvalue().encode('utf-8-sig'))
    output.seek(0)

    safe_name = class_name.replace('/', '_').replace('\\', '_').replace(':', '')
    filename = f'{safe_name}_第{cuc_num}讲.csv'

    return send_file(output, mimetype='text/csv', as_attachment=True, download_name=filename)

@app.route('/export/image', methods=['POST'])
def export_backend_image():
    req_start = time.time()

    html_content = request.form.get('html', '')
    cuc_num = request.form.get('cucNum', '1')
    default_filename = f'xiaohou_stats_{cuc_num}.png'
    filename = request.form.get('filename', default_filename)
    selector = request.form.get('selector', '#capture-area')
    width_param = request.form.get('width', '1920')
    zoom_param = request.form.get('zoom', '1.0')

    logging.info(f"[EXPORT] start ip={request.remote_addr} filename={filename} selector={selector} html_len={len(html_content)} width={width_param} zoom={zoom_param}")

    if not html_content:
        logging.warning("[EXPORT] missing html content")
        return "Missing HTML content", 400

    try:
        with sync_playwright() as p:
            browser_launch_kwargs = {'headless': True}
            executable_path = resolve_export_browser_executable()
            if executable_path:
                browser_launch_kwargs['executable_path'] = executable_path

            logging.info("[EXPORT] launching browser")
            browser = p.chromium.launch(**browser_launch_kwargs)
            try:
                viewport_width = int(float(width_param))
            except Exception:
                viewport_width = 1920

            context = browser.new_context(viewport={'width': viewport_width, 'height': 3000}, device_scale_factor=2)
            page = context.new_page()

            logging.info("[EXPORT] set_content begin")
            page.set_content(html_content, wait_until="load")
            logging.info("[EXPORT] set_content done")

            page.add_style_tag(content=f"""
                nav, button, .no-print {{ display: none !important; }}
                body {{ background-color: white !important; overflow: hidden !important; }}
                #capture-area {{ zoom: {zoom_param} !important; transform-origin: top left !important; box-shadow: none !important; border: none !important; margin: 0 auto !important; max-width: none !important; width: 100% !important; }}
            """)

            logging.info("[EXPORT] screenshot begin")
            locator = page.locator(selector)
            screenshot_bytes = locator.screenshot(type="png", omit_background=True)
            logging.info(f"[EXPORT] screenshot done size={len(screenshot_bytes)}")

            browser.close()
            elapsed = time.time() - req_start
            logging.info(f"[EXPORT] done elapsed={elapsed:.2f}s")

            return send_file(io.BytesIO(screenshot_bytes), mimetype='image/png', as_attachment=True, download_name=filename)
    except Exception as e:
        elapsed = time.time() - req_start
        logging.exception(f"[EXPORT] failed elapsed={elapsed:.2f}s error={e}")
        return f"截图服务出错: {str(e)}", 500

@app.route('/report/student')
def student_report_view():
    token = session.get('token')
    class_id = request.args.get('classId', '').strip()
    student_name = request.args.get('studentName', '').strip()
    student_id = request.args.get('studentId', '').strip()
    class_year = request.args.get('classYear', '').strip()
    class_name = request.args.get('className', '班级报告').strip()

    if not class_year: class_year = str(datetime.now().year)
    if not token or not class_id or not student_name: return "Missing params", 400

    return render_template('student_report.html',
                           student_name=student_name,
                           params={ 'classId': class_id, 'studentId': student_id, 'studentName': student_name, 'classYear': class_year, 'className': class_name })

@app.route('/api/get_class_roster')
def get_class_roster():
    token = session.get('token')
    class_id = request.args.get('classId', '').strip()
    mode = request.args.get('mode', 'latest').strip() # 支持 'deep' 或 'latest'
    teacherType = session.get('teacherType', '0')

    if not token or not class_id:
        return jsonify({'error': 'Missing params'}), 400

    # 1. 获取课表
    lessons = fetch_class_curriculum(token, class_id)
    if not lessons:
        return jsonify({'students': [], 'msg': '无法获取课表信息'})

    # 2. 确定要扫描的讲次 (target_nums)
    target_nums = set()
    today = datetime.now().strftime('%Y-%m-%d')

    if mode == 'deep':
        # [深搜模式] 扫描所有已开课的讲次，聚合所有出现过的学生
        found_any = False
        for l in lessons:
            if l.get('date') and l['date'] <= today:
                target_nums.add(l['num'])
                found_any = True
        # 如果一节课都没开，就扫第1讲试试
        if not found_any and lessons: target_nums.add(lessons[0]['num'])
    else:
        # [最新模式] 只扫最近的一节已结课的讲次 (速度快)
        target_lesson = lessons[0]
        for l in reversed(lessons):
            if l.get('date') and l['date'] <= today:
                target_lesson = l
                break
        target_nums.add(target_lesson['num'])

    # 3. 并发抓取名单
    all_students_map = {}

    def fetch_names_only(c_num):
        try:
            # 调用 data_loader 中的 fetch_data
            # 注意：现在的 fetch_data 返回 6 个元素的元组
            result = fetch_data(class_id, token, c_num, teacherType)

            # 检查返回值是否有效 (result[0] 是 headers，如果是 None 表示失败)
            if result and result[0] is not None:
                rows = result[1] # rows 在索引 1
                # 提取简单的 name/id 字典
                return [{'name': r['name'], 'id': r.get('studentId', ''), 'progress': r.get('progress_percent', 0)} for r in rows if r.get('name')]
        except Exception as e:
            print(f"Error fetching roster for lesson {c_num}: {e}")
            return []
        return []

    # 限制并发数为 10，避免瞬间爆接口
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_names_only, num) for num in target_nums]
        for future in as_completed(futures):
            results = future.result()
            for stu in results:
                key = stu['name']
                # 如果这个学生之前没存过，或者当前这个记录有 ID (有些旧记录可能没 ID)，则更新
                if key not in all_students_map or (not all_students_map[key]['id'] and stu['id']):
                    all_students_map[key] = stu

    # 4. 排序并返回
    # 按姓名排序，符合点名习惯
    sorted_students = sorted(list(all_students_map.values()), key=lambda x: x['name'])

    return jsonify({'students': sorted_students, 'count': len(sorted_students), 'mode': mode})

@app.route('/api/report/student/data')
def student_report_data_api():
    token = session.get('token')
    class_id = request.args.get('classId', '').strip()
    student_name = request.args.get('studentName', '').strip()
    student_id = request.args.get('studentId', '').strip()
    class_year = request.args.get('classYear', '').strip()
    class_name = request.args.get('className', '').strip()
    teacherType = session.get('teacherType', '0')

    stats_map = {}
    history = []

    lessons = fetch_class_curriculum(token, class_id)

    if student_id:
        history, stats_map = fetch_student_detailed_history_v2(
            token, class_id, student_name, student_id, class_year, lessons, class_name, teacherType
        )

    return jsonify({'stats': stats_map, 'history': history})

if __name__ == '__main__':
    # 检测自签证书，有则启用 HTTPS（剪贴板 API 需要安全上下文）
    cert_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cert.pem')
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'key.pem')
    has_ssl = os.path.exists(cert_file) and os.path.exists(key_file)

    print("========================================")
    print("   XiaoHou Insight - 学情分析助手 Pro")
    if has_ssl:
        print("   Listening on: https://0.0.0.0:6927")
        print("   [HTTPS] 剪贴板功能已启用（cheroot + ssl）")
    else:
        print("   Listening on: http://0.0.0.0:6927")
        print("   [HTTP] 剪贴板功能不可用（需 HTTPS）")
        print("   提示: 运行证书生成脚本后即可启用 HTTPS")
    print("========================================")

    if has_ssl:
        # HTTPS：使用 cheroot（原生支持 SSL、多线程、跨平台 Win/Mac/Linux）
        try:
            from cheroot.wsgi import Server as CherootServer
            from cheroot.ssl.builtin import BuiltinSSLAdapter

            server = CherootServer(('0.0.0.0', 6927), app, numthreads=20)
            server.ssl_adapter = BuiltinSSLAdapter(cert_file, key_file)
            try:
                server.start()
            except KeyboardInterrupt:
                server.stop()
        except ImportError:
            # 没装 cheroot 时回退 Flask 内置 HTTPS（开发服务器）
            print("[警告] 未安装 cheroot，回退 Flask 内置 HTTPS（不推荐用于多人）")
            app.run(debug=False, host='0.0.0.0', port=6927,
                    ssl_context=(cert_file, key_file), threaded=True)
    else:
        # HTTP：使用 waitress（稳定、高并发）
        try:
            from waitress import serve
            serve(app, host='0.0.0.0', port=6927, threads=20, connection_limit=200)
        except ImportError:
            app.run(debug=False, host='0.0.0.0', port=6927)