import io
import csv
import json
import time
import os
import secrets
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, Response, request, render_template, redirect, url_for, jsonify, send_file, session
from playwright.sync_api import sync_playwright

from data_loader import (
    fetch_data, perform_login, fetch_class_list,
    fetch_class_curriculum, fetch_student_overall_stats, fetch_student_detailed_history_v2,
    fetch_class_detail
)

app = Flask(__name__)

# [安全配置]
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
# 设置 Session 有效期为 30 天，模拟“自动登录”体验，但数据存在客户端
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

@app.route('/', methods=['GET', 'POST'])
def index():
    # [关键修改] 从 Session 获取当前用户的 Token，实现多人隔离
    token = session.get('token')
    teacherType = session.get('teacherType', '0')
    username = session.get('username', '')

    # 辅助参数 (单讲看板用)
    req_token = request.values.get('token') # 某些URL可能还带token
    if req_token: token = req_token         # 优先使用URL里的

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

        download_url = url_for('download_csv', token=token, classId=class_id, cucNum=cuc_num, className=class_name)

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
                               current_token=token,
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
                                   username=username, token=token, search_range=search_range, teacherType=teacherType)
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
                                   username=username, token=token, search_range=search_range, teacherType=teacherType)

    # === 默认场景: 显示登录页 ===
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear() # [v119] 退出登录只需清空 Cookie
    return redirect(url_for('index'))

@app.route('/api/get_curriculum')
def get_curriculum():
    token = request.args.get('token')
    class_id = request.args.get('classId')
    lessons = fetch_class_curriculum(token, class_id)
    return jsonify({'lessons': lessons})

@app.route('/download')
def download_csv():
    token = request.args.get('token')
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
    html_content = request.form.get('html', '')
    cuc_num = request.form.get('cucNum', '1')
    default_filename = f'xiaohou_stats_{cuc_num}.png'
    filename = request.form.get('filename', default_filename)
    selector = request.form.get('selector', '#capture-area')
    width_param = request.form.get('width', '1920')
    zoom_param = request.form.get('zoom', '1.0')

    if not html_content: return "Missing HTML content", 400

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try: viewport_width = int(float(width_param))
            except: viewport_width = 1920

            context = browser.new_context(viewport={'width': viewport_width, 'height': 3000}, device_scale_factor=2)
            page = context.new_page()
            page.set_content(html_content, wait_until="load")

            page.add_style_tag(content=f"""
                nav, button, .no-print {{ display: none !important; }}
                body {{ background-color: white !important; overflow: hidden !important; }}
                main {{ zoom: {zoom_param} !important; transform-origin: top left !important; }}
                #capture-area {{ box-shadow: none !important; border: none !important; margin: 0 auto !important; max-width: none !important; width: 100% !important; }}
            """)

            locator = page.locator(selector)
            screenshot_bytes = locator.screenshot(type="png", omit_background=True)
            browser.close()

            return send_file(io.BytesIO(screenshot_bytes), mimetype='image/png', as_attachment=True, download_name=filename)
    except Exception as e:
        print(f"Playwright Error: {e}")
        return f"截图服务出错: {str(e)}", 500

@app.route('/report/student')
def student_report_view():
    token = request.args.get('token', '').strip()
    class_id = request.args.get('classId', '').strip()
    student_name = request.args.get('studentName', '').strip()
    student_id = request.args.get('studentId', '').strip()
    class_year = request.args.get('classYear', '').strip()
    class_name = request.args.get('className', '班级报告').strip()

    if not class_year: class_year = str(datetime.now().year)
    if not token or not class_id or not student_name: return "Missing params", 400

    return render_template('student_report.html',
                           student_name=student_name,
                           params={ 'token': token, 'classId': class_id, 'studentId': student_id, 'studentName': student_name, 'classYear': class_year, 'className': class_name })

@app.route('/api/get_class_roster')
def get_class_roster():
    token = request.args.get('token', '').strip()
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
    token = request.args.get('token', '').strip()
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
    print("========================================")
    print("   XiaoHou Insight - 学情分析助手 Pro")
    print("   Listening on: http://0.0.0.0:6927")
    print("========================================")

    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=6927, threads=20, connection_limit=200)
    except ImportError:
        print("[警告] 未安装 waitress，请安装以支持并发访问。")
        app.run(debug=False, host='0.0.0.0', port=6927)