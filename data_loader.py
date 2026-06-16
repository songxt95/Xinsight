import requests
import json
import os
import time
import datetime
import logging
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

# 配置日志
logging.basicConfig(
    filename='xiaohou_debug.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

# === 课表内存缓存 ===
# key: (token_prefix, class_id), value: (lessons, timestamp)
_curriculum_cache = {}
_CURRICULUM_CACHE_TTL = 86400  # 1 天

# === 班级列表内存缓存 ===
# key: (token_prefix, search_range, teacherType), value: (class_groups, timestamp)
_class_list_cache = {}
_CLASS_LIST_CACHE_TTL = 300  # 5 分钟

_PERFECT_SCORE_VALUES = {'100', '答题正确', '已订正'}
_NON_ANSWERED_KEYWORDS = ('未打开', '未作答', '未运行')

def _token_fingerprint(token):
    """用 token 哈希做用户维度隔离，避免不同账号前缀相同导致缓存串用"""
    if not token:
        return ''
    return hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]


def _get_cache_key(token, class_id):
    return (_token_fingerprint(token), class_id)

# ... (perform_login, fetch_class_curriculum, fetch_class_list, fetch_class_detail 保持不变) ...
def perform_login(username, password, teacherType='0'):
    url = 'https://rest.xiaohoucode.com/api/uc/teachers/login'
    data = {'username': username, 'password': password, 'cityCode': '010', 'type': teacherType}
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    try:
        response = requests.post(url, json=data, headers=headers, timeout=5)
        if response.status_code != 200: return None, f"HTTP {response.status_code}"
        result = response.json()
        if result.get('status') == 200: return result['data'].get('accessToken'), None
        return None, result.get('message', '登录失败')
    except Exception as e: return None, str(e)

def fetch_class_curriculum(token, class_id):
    cache_key = _get_cache_key(token, class_id)
    now = time.time()

    # 命中缓存且未过期则直接返回
    if cache_key in _curriculum_cache:
        cached_lessons, cached_at = _curriculum_cache[cache_key]
        if now - cached_at < _CURRICULUM_CACHE_TTL:
            return cached_lessons

    url = f"https://rest.xiaohoucode.com/api/core/teachers/findCurriculumByClass?classId={class_id}"
    headers = { "accept": "application/json, text/plain, */*", "authorization": token, "User-Agent": "Mozilla/5.0", "Origin": "https://www.xiaohoucode.com" }
    try:
        response = requests.get(url, headers=headers, timeout=5)
        res = response.json()
        data = res.get('data', [])
        lessons = []
        for d in data:
            lessons.append({ 'num': d.get('classNum', 0), 'name': d.get('lessonName', '未知'), 'date': d.get('classDate', ''), 'start': d.get('startTime', '') })
        lessons.sort(key=lambda x: x['num'])
        # 写入缓存
        _curriculum_cache[cache_key] = (lessons, now)
        return lessons
    except: return []

def fetch_class_list(token, search_range=180, teacherType="0"):
    # 检查班级列表内存缓存
    list_cache_key = (_token_fingerprint(token), search_range, teacherType)
    now = time.time()
    if list_cache_key in _class_list_cache:
        cached_groups, cached_at = _class_list_cache[list_cache_key]
        if now - cached_at < _CLASS_LIST_CACHE_TTL:
            logging.info(f"Class List Cache HIT for range={search_range}")
            return cached_groups, None

    url = "https://rest.xiaohoucode.com/api/core/teachers/partclasses"
    headers = { "Host": "rest.xiaohoucode.com", "Authorization": token, "User-Agent": "Mozilla/5.0", "Content-Type": "application/json", "Origin": "https://www.xiaohoucode.com" }
    groups = { 'open': [], 'closed': [], 'self': [] }

    today_date = datetime.datetime.now().date()

    logging.info(f"--- Fetching Class List (Range: {search_range}) ---")

    try:
        for type_val, group_key, time_range in [(1, 'open', 0), (0, 'closed', search_range)]:
            payload = {"type": type_val, "range": time_range, "teacherType": teacherType, "bizType": 10000}
            response = requests.post(url, json=payload, headers=headers, timeout=5)
            if response.status_code != 200: continue

            try:
                res_json = response.json()
            except:
                logging.error(f"Class List JSON Decode Error for {group_key}")
                continue

            data_obj = res_json.get('data')
            if not data_obj: continue

            rows = data_obj.get('rows', [])
            for r in rows:
                if not r.get('id'): continue

                curr_list = r.get('curriculumnList', [])
                preloaded_lessons = []
                if curr_list:
                    for lesson in curr_list:
                        preloaded_lessons.append({
                            'num': lesson.get('classNum', 0),
                            'name': lesson.get('lessonName', '未知'),
                            'date': lesson.get('classDate', ''),
                            'start': lesson.get('startTime', '')
                        })
                    preloaded_lessons.sort(key=lambda x: x['num'])

                is_live = (r.get('classType', 0) != 0)

                # 结课当天的课保留在开课中
                end_time_str = r.get('endTime', '2099-12-31')
                try:
                    end_time = datetime.datetime.strptime(end_time_str, "%Y-%m-%d").date()
                except:
                    end_time = datetime.datetime.strptime('2099-12-31', "%Y-%m-%d").date() # 结课时间API有问题，兜底
                status_val = type_val
                if end_time >= today_date:
                    status_val = 1
                
                item = {
                    'id': r.get('id'),
                    'name': r.get('className') or r.get('name'),
                    'courseId': r.get('courseId', ''),
                    'time': r.get('classTimeDisplay', '') or ("直播时段" if is_live else "自学"),
                    'count': len(preloaded_lessons) if preloaded_lessons else r.get('classNum', 0),
                    'type': '直播' if is_live else '自学',
                    'status': status_val,
                    'year': str(r.get('year', '')),
                    'termName': r.get('termName', ''),
                    'startDate': r.get('startTime', ''),
                    'endTime': r.get('endTime', ''),
                    'lessons': preloaded_lessons
                }

                

                if is_live:
                    if status_val == 1: groups['open'].append(item)
                    else: groups['closed'].append(item)
                else: groups['self'].append(item)

        # 写入班级列表缓存
        _class_list_cache[list_cache_key] = (groups, now)
        # 预热课表缓存：将返回中自带的 curriculumnList 写入内存缓存
        for group in groups.values():
            for item in group:
                if item.get('lessons'):
                    warm_key = (_token_fingerprint(token), item['id'])
                    if warm_key not in _curriculum_cache:
                        _curriculum_cache[warm_key] = (item['lessons'], now)
        logging.info(f"Class List Fetched & Cached for range={search_range}")
        return groups, None
    except Exception as e: return None, f"Err: {str(e)}"

def fetch_class_detail(token, class_id):
    # 默认返回值，防止 API 失败导致页面报错
    current_year = str(datetime.datetime.now().year)
    result = {'id': class_id, 'name': '班级详情', 'year': current_year}

    headers = {
        "Authorization": token,
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*"
    }

    # 1. 获取年份 (pullInfo 接口)
    try:
        url_year = f"https://rest.xiaohoucode.com/api/core/trace/common/pullInfo?key=classId&value={class_id}"
        res_year = requests.get(url_year, headers=headers, timeout=2)
        if res_year.status_code == 200:
            data = res_year.json().get('data', {})
            # 用户指定提取字段: year
            if data and data.get('year'):
                result['year'] = str(data['year'])
    except Exception as e:
        logging.error(f"Fetch Year HTTP Error: {res_year.status_code}")
    
    # 2. 获取班级名称 (detail 接口)
    try:
        url_name = f"https://rest.xiaohoucode.com/api/core/class/ground/public/detail?classId={class_id}"
        res_name = requests.get(url_name, headers=headers, timeout=2)
        if res_name.status_code == 200:
            data = res_name.json().get('data', {})
            # 用户指定提取字段: courseName
            if data and data.get('courseName'):
                result['name'] = data['courseName']
    except Exception as e:
        logging.error(f"Fetch Name HTTP Error: {res_name.status_code}")
    
    # 记录日志，看看优化效果
    logging.info(f"Detail Fetched: {result['name']} ({result['year']})")

    return result

# === [v121 修改] 增加深度 Debug 日志 ===
def _is_perfect_answer(score):
    return str(score) in _PERFECT_SCORE_VALUES


def _is_answered_score(score):
    if score is None:
        return False
    text = str(score).strip()
    if not text or text == '-':
        return False
    return all(keyword not in text for keyword in _NON_ANSWERED_KEYWORDS)


def fetch_data(class_id, token, cuc_num, teacherType="0"):
    if not cuc_num: cuc_num = 1

    # 1. 打印请求信息
    masked_token = token[:10] + "..." if token else "None"
    logging.info(f"======== [DEBUG] Fetch Data Request ========")
    logging.info(f"ClassID: {class_id}, Lesson: {cuc_num}")
    logging.info(f"Token: {masked_token}")

    url = f"https://rest.xiaohoucode.com/api/core/stats/v2/stu/answers?classId={class_id}&cityCode=010&cucNum={cuc_num}&teacherType={teacherType}"
    logging.info(f"URL: {url}")

    headers = {"Authorization": token, "User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(url, headers=headers, timeout=10)

        # 2. 打印原始响应文本 (只取前1000个字符防止日志爆炸)
        logging.info(f"Response Status: {response.status_code}")
        logging.info(f"Response Body (First 1000 chars): {response.text[:1000]}")

        if response.status_code != 200:
            logging.error(f"Fetch Data HTTP Error: {response.status_code}")
            return None, [], [], [], [], {"error": f"HTTP {response.status_code}"}

        data_json = response.json()

        if data_json.get('status') != 200:
            err_msg = data_json.get('message', 'API Error')
            logging.warning(f"API Logic Error: {err_msg}")
            return None, [], [], [], [], {"error": err_msg}

        data_obj = data_json.get('data')
        if not data_obj:
            logging.warning("Data object is EMPTY. This implies Class ID or Lesson Num might be invalid, or no permission.")
            return ['姓名', '进度'], [], [], [], [], {"error": "暂无该讲次数据", "student_count": 0, "avg_progress": 0}

        students = data_obj.get('body', [])
        if students is None: students = []

        logging.info(f"Parsed Students Count: {len(students)}")

        if not students:
            return ['姓名', '进度'], [], [], [], [], {"error": "暂无学生数据", "student_count": 0, "avg_progress": 0}

        bundle_types = [('classPracticeBundle', '课堂'), ('homeworkPracticeBundle', '课后'), ('examBundle', '考试')]
        practice_meta = {}
        for stu in students:
            for bundle_key, label in bundle_types:
                bundle = stu.get(bundle_key)
                if not bundle: continue
                for p in bundle.get('practice', []):
                    uid = f"{bundle_key}_{p.get('bundleOrder', 0)}_{p.get('practiceOrder', 0)}"
                    if uid not in practice_meta:
                        practice_meta[uid] = {
                            'name': p['practiceName'],
                            'order_val': (int(p.get('bundleOrder', 0)), int(p.get('practiceOrder', 0))),
                            'display_order': f"{p.get('bundleOrder',0)}.{p.get('practiceOrder',0)}",
                            'category': label
                        }

        sorted_uids = sorted(practice_meta.keys(), key=lambda x: practice_meta[x]['order_val'])

        practice_names = []
        for uid in sorted_uids:
            item = practice_meta[uid]
            order_prefix = str(item['display_order'])
            raw_name = item['name']
            if order_prefix and not raw_name.startswith(order_prefix):
                practice_names.append(f"{order_prefix} {raw_name}")
            else:
                practice_names.append(raw_name)

        practice_orders = [practice_meta[uid]['display_order'] for uid in sorted_uids]
        practice_categories = [practice_meta[uid]['category'] for uid in sorted_uids]

        total_tasks = len(sorted_uids)
        rows = []

        for i, stu in enumerate(students):
            s_name = stu.get('studentName') or '未知学员'
            s_id = stu.get('studentId', '')

            row_data = {'name': s_name, 'studentId': s_id, 'scores': []}
            score_map = {}
            for bundle_key, _ in bundle_types:
                bundle = stu.get(bundle_key)
                if bundle:
                    for p in bundle.get('practice', []):
                        uid = f"{bundle_key}_{p.get('bundleOrder', 0)}_{p.get('practiceOrder', 0)}"
                        score_map[uid] = p.get('answerResult', '-')
            completed = 0
            for uid in sorted_uids:
                score = score_map.get(uid, '-')
                if score in ['-', '']: score = '未打开'
                row_data['scores'].append(score)
                if _is_perfect_answer(score): completed += 1
            row_data['progress_text'] = f"{completed}/{total_tasks}"
            row_data['progress_percent'] = (completed / total_tasks * 100) if total_tasks > 0 else 0
            rows.append(row_data)

        rows.sort(key=lambda x: x['progress_percent'], reverse=True)
        class_completed = sum([int(r['progress_text'].split('/')[0]) for r in rows])
        class_total = sum([int(r['progress_text'].split('/')[1]) for r in rows])
        avg = int((class_completed / class_total) * 100) if class_total > 0 else 0

        return ['姓名', '进度'] + practice_names, rows, practice_names, practice_orders, practice_categories, {'student_count': len(students), 'avg_progress': avg}

    except Exception as e:
        logging.error(f"Fetch Data Critical Error: {str(e)}")
        # 记录详细堆栈
        import traceback
        logging.error(traceback.format_exc())
        return None, [], [], [], [], {"error": f"Error: {str(e)}"}

# ... (后面的函数 fetch_student_overall_stats 等保持不变) ...
def fetch_lesson_question_snapshot(class_id, token, cuc_num, teacherType="0"):
    if not cuc_num:
        cuc_num = 1

    url = f"https://rest.xiaohoucode.com/api/core/stats/v2/stu/answers?classId={class_id}&cityCode=010&cucNum={cuc_num}&teacherType={teacherType}"
    headers = {"Authorization": token, "User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None, {"error": f"HTTP {response.status_code}"}

        payload = response.json()
        if payload.get('status') != 200:
            return None, {"error": payload.get('message', 'API Error')}

        students_raw = (payload.get('data') or {}).get('body') or []

        bundle_defs = [
            ('classPracticeBundle', '课堂'),
            ('homeworkPracticeBundle', '课后'),
            ('examBundle', '考试')
        ]

        question_meta = {}
        students = []

        for stu in students_raw:
            student_name = stu.get('studentName') or '未知学员'
            student_id = stu.get('studentId', '')
            answer_map = {}

            for bundle_key, bundle_label in bundle_defs:
                bundle = stu.get(bundle_key)
                if not bundle:
                    continue
                for p in bundle.get('practice', []):
                    practice_id = str(p.get('practiceId') or '').strip()
                    if not practice_id:
                        continue

                    uid = f"{bundle_key}:{practice_id}"
                    answer_result = p.get('answerResult', '-')
                    answer_map[uid] = answer_result

                    if uid not in question_meta:
                        practice_name = p.get('practiceName') or practice_id
                        bundle_order = int(p.get('bundleOrder', 0) or 0)
                        practice_order = int(p.get('practiceOrder', 0) or 0)
                        order_text = f"{bundle_order}.{practice_order}"
                        if not practice_name.startswith(order_text):
                            display_name = f"{order_text} {practice_name}"
                        else:
                            display_name = practice_name

                        question_meta[uid] = {
                            'uid': uid,
                            'practiceId': practice_id,
                            'practiceName': practice_name,
                            'displayName': display_name,
                            'bundleKey': bundle_key,
                            'bundleLabel': bundle_label,
                            'bundleOrder': bundle_order,
                            'practiceOrder': practice_order,
                            'sortKey': (bundle_order, practice_order, practice_id)
                        }

            students.append({
                'studentId': student_id,
                'studentName': student_name,
                'answers': answer_map
            })

        questions = sorted(question_meta.values(), key=lambda x: x['sortKey'])
        question_uids = [q['uid'] for q in questions]

        student_count = len(students)
        question_count = len(question_uids)

        class_correct = 0
        class_answered = 0
        class_cells = []

        for uid in question_uids:
            q_correct = 0
            q_answered = 0
            for stu in students:
                score = stu['answers'].get(uid, '-')
                if _is_answered_score(score):
                    q_answered += 1
                    class_answered += 1
                    if _is_perfect_answer(score):
                        q_correct += 1
                        class_correct += 1
            class_cells.append({
                'questionUid': uid,
                'correctCount': q_correct,
                'answeredCount': q_answered,
                'accuracy': round((q_correct / q_answered) * 100, 2) if q_answered > 0 else None,
                'completionRate': round((q_correct / student_count) * 100, 2) if student_count > 0 else None
            })

        for stu in students:
            answered = 0
            correct = 0
            cells = []
            for uid in question_uids:
                score = stu['answers'].get(uid, '-')
                cell_answered = _is_answered_score(score)
                cell_correct = _is_perfect_answer(score)
                if cell_answered:
                    answered += 1
                    if cell_correct:
                        correct += 1
                cells.append({
                    'questionUid': uid,
                    'raw': score,
                    'answered': cell_answered,
                    'correct': cell_correct,
                    'accuracy': 100.0 if cell_correct else (0.0 if cell_answered else None),
                    'completionRate': 100.0 if cell_correct else 0.0
                })

            stu['correctCount'] = correct
            stu['answeredCount'] = answered
            stu['accuracy'] = round((correct / answered) * 100, 2) if answered > 0 else None
            stu['completionRate'] = round((correct / question_count) * 100, 2) if question_count > 0 else None
            stu['cells'] = cells

        total_question_count = student_count * question_count

        summary = {
            'studentCount': student_count,
            'questionCount': question_count,
            'totalQuestionCount': total_question_count,
            'correctCount': class_correct,
            'answeredCount': class_answered,
            'accuracy': round((class_correct / class_answered) * 100, 2) if class_answered > 0 else None,
            'completionRate': round((class_correct / total_question_count) * 100, 2) if total_question_count > 0 else None
        }

        return {
            'lessonNum': int(cuc_num),
            'questions': questions,
            'students': students,
            'classCells': class_cells,
            'summary': summary
        }, None
    except Exception as e:
        logging.error(f"fetch_lesson_question_snapshot error: {str(e)}")
        return None, {"error": str(e)}


def fetch_student_overall_stats(token, class_ids, student_id, year):
    if not year: year = str(datetime.datetime.now().year)
    url = "https://rest.xiaohoucode.com/api/core/stats/s-practice/query/stats"
    params = {"classIds": class_ids, "queryType": "2", "year": str(year), "studentId": student_id, "search": ""}
    headers = {"Authorization": token, "User-Agent": "Mozilla/5.0", "Origin": "https://www.xiaohoucode.com"}
    logging.info(f"--- Calling Stats API ---")
    try:
        res = requests.get(url, params=params, headers=headers, timeout=2).json()
        return res.get('data', {}).get('stats', [])
    except Exception as e:
        return []

def fetch_practice_api_impl(token, class_ids, student_id, year, practice_try_type, page_size):
    url = "https://rest.xiaohoucode.com/api/core/stats/s-practice/query/practice"
    params = {"classIds": class_ids, "pageIndex": "1", "pageSize": str(page_size), "year": str(year), "queryType": "2", "practiceTryType": str(practice_try_type), "studentId": student_id, "search": "", "lessonDay": ""}
    headers = {"Authorization": token, "User-Agent": "Mozilla/5.0", "Origin": "https://www.xiaohoucode.com"}
    logging.info(f"--- Calling Practice API (Type {practice_try_type}) ---")
    try:
        res = requests.get(url, params=params, headers=headers, timeout=5).json()
        return res.get('data', {}).get('practice', [])
    except Exception as e:
        return []

def fetch_student_classes(token, student_id, year):
    url = "https://rest.xiaohoucode.com/api/core/stats/s-practice/query/class"
    params = { "studentId": student_id, "year": str(year), "termList": ["1", "2", "3", "4", "5", "6"] }
    headers = {"Authorization": token, "User-Agent": "Mozilla/5.0", "Origin": "https://www.xiaohoucode.com"}
    try:
        res = requests.get(url, params=params, headers=headers, timeout=5).json()
        return res.get('data', {}).get('classDtoList', [])
    except: return []

def fetch_student_detailed_history_v2(token, main_class_id, student_name, student_id, year, lessons, target_class_name, teacherType="0"):
    logging.info(f"Start Aggregation for {student_name} (Year: {year}, Target: {target_class_name})")

    found_class_ids = set()
    found_class_ids.add(main_class_id)

    student_classes = fetch_student_classes(token, student_id, year)
    if student_classes:
        for c in student_classes:
            if c.get('className') == target_class_name:
                found_class_ids.add(c.get('classId'))

    all_class_ids_list = list(found_class_ids)

    stats = fetch_student_overall_stats(token, all_class_ids_list, student_id, year)
    stats_map = {item['type']: item['count'] for item in stats}

    for q_type in ['0', '1', '2']:
        count = stats_map.get(q_type, 0)
        if count > 0:
            records = fetch_practice_api_impl(token, all_class_ids_list, student_id, year, q_type, count)
            for r in records:
                if r.get('classId'): found_class_ids.add(r.get('classId'))

    valid_lessons = [l for l in lessons if not l.get('date') or l.get('date') <= datetime.datetime.now().strftime('%Y-%m-%d')]
    tasks = []
    for cid in found_class_ids:
        for lesson in valid_lessons:
            tasks.append({'classId': cid, 'num': lesson['num'], 'name': lesson['name']})

    merged_history = {}

    def fetch_task(task):
        try:
            result = fetch_data(task['classId'], token, task['num'], teacherType)
            # [Fix] 检查返回值
            if not result or not result[0]: return None

            rows = result[1]
            headers = result[2]
            target_row = next((r for r in rows if r['name'] == student_name), None)
            if target_row:
                return {
                    'num': task['num'],
                    'name': task['name'],
                    'progress_percent': target_row['progress_percent'],
                    'progress_text': target_row['progress_text'],
                    'headers': headers,
                    'scores': target_row['scores']
                }
        except: pass
        return None

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(fetch_task, t) for t in tasks]
        for future in as_completed(futures):
            res = future.result()
            if res:
                num = res['num']
                if num not in merged_history:
                    merged_history[num] = res
                else:
                    old_res = merged_history[num]
                    if (res['progress_percent'] > old_res['progress_percent']) or \
                       (res['progress_percent'] == old_res['progress_percent'] and len(res['headers']) > len(old_res['headers'])):
                        merged_history[num] = res

    final_list = list(merged_history.values())
    final_list.sort(key=lambda x: x['num'])

    complete_history = []
    data_map = {item['num']: item for item in final_list}
    for l in valid_lessons:
        if l['num'] in data_map:
            complete_history.append(data_map[l['num']])
        else:
            complete_history.append({ 'num': l['num'], 'name': l['name'], 'progress_percent': 0, 'progress_text': '0/0', 'headers': [], 'scores': [] })

    return complete_history, stats_map