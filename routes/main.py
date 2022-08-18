from flask import Blueprint, flash, g, redirect, request, session, url_for, jsonify, send_file, make_response, \
    render_template
from models.all_models import *
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import login_user, login_required, logout_user, current_user

bp = Blueprint('dnapi', __name__, url_prefix='/')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = Users.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user, remember=True)
            return redirect(url_for('index'))
        else:
            flash('Wrong username or password', category='warning')
    return render_template("login.html")


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        repeat_password = request.form.get('repeat_password')
        user = Users.query.filter_by(username=username).first()
        if user:
            flash('username already exists', category='warning')
        elif len(username) < 3 or len(username) > 20:
            flash('username must be of length 3-20 characters', category='warning')
        elif len(password) < 3 or len(password) > 20:
            flash('password must be of length 3-20 characters', category='warning')
        elif password != repeat_password:
            flash('Passwords are not identical', category='warning')
        else:
            new_user = Users(username=username, password=generate_password_hash(password, method='sha256'))
            db.session.add(new_user)
            db.session.commit()
            flash('Account created', category='success')
            return redirect(url_for('dnapi.login'))
    # if request.method == 'GET':
    return render_template("register.html")


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully', category='success')
    return redirect(url_for('dnapi.login'))


@bp.route('/level1/intro')
def intro():
    chapters = left_chapter_menu_helper()
    return render_template("games/intro.html", chapters=chapters)


@bp.route('/level1/chapter/<chapter_id>')
@login_required
def chapter(chapter_id):
    """
    view function for quiz page
    @param chapter_id: the chapter id for showing related quiz questions
    @return: the template for quiz
    """
    chapter_dump, chapters, questions_dump = left_chapter_menu_helper(chapter_id)
    return render_template("games/chapter.html", questions=questions_dump, chapter=chapter_dump,
                           chapters=chapters)


@bp.route('/quiz/<chapter_id>/submit', methods=['POST'])
@login_required
def quiz_submit(chapter_id):
    """
    function for receiving quiz answers
    @param chapter_id: the chapter id for showing related quiz questions
    @return: show result
    """
    form = request.form
    chapter_dump, chapters, questions_dump = left_chapter_menu_helper(chapter_id)

    # check choice submitted if right wrong or miss
    cur_score = 0
    db.engine.execute("delete from answer where answer.choice_id in  ( select answer.choice_id from answer, choice, chapter, users, question where answer.choice_id = choice.id and choice.question_id = question.id and question.chapter_id = chapter.id and chapter.id = %s and users.id = %s )" % (chapter_id, current_user.id))
    db.engine.execute("delete from score where chapter_id = %s and user_id=%s" % (chapter_id, current_user.id) )
    for question in questions_dump:
        submitted_answers = set(form.getlist(question['id']))
        for aws_id in submitted_answers:
            new_answer = Answer(
                user_id=current_user.id,
                choice_id=aws_id
            )
            db.session.add(new_answer)
            db.session.commit()
        selected_correct = 0
        missed_wrong = 0
        if not form.get(question['id']):
            question['missed'] = True
        for choice in question['choices']:
            if choice['correctness']:
                if choice['id'] in submitted_answers:
                    choice['state'] = 'correct'
                    selected_correct += 1
                else:
                    choice['state'] = 'missed'
            else:
                if choice['id'] in submitted_answers:
                    choice['state'] = 'wrong'
                else:
                    missed_wrong += 1
        if question['type'] in ['choose_one', 'grid']:
            if selected_correct > 0:
                question['score'] = question['point']
                cur_score += question['point']
            else:
                question['score'] = 0
        else:
            correct_sum = selected_correct + missed_wrong
            question['score'] = question['point'] * 0 if correct_sum == 0 else len(question['choices']) / correct_sum
    new_score = Score(
        score = cur_score,
        user_id=current_user.id,
        chapter_id = chapter_id
    )
    db.session.add(new_score)
    db.session.commit()
    ranking = get_ranking(chapter_id)
    return render_template("games/quiz_result.html", questions=questions_dump,
                           chapter=chapter_dump, score=cur_score, chapters=chapters, ranking=ranking)


def left_chapter_menu_helper(chapter_id=0):
    """
        helper function to return all data needed for the chapter menu
    """
    if chapter_id != 0:
        questions = Question.query.filter_by(chapter_id=chapter_id).order_by(Question.id).all()
        questions_dump = questionswithanswers_schema.dump(questions)
        chapter = Chapter.query.get(chapter_id)
        chapter_dump = chapter_schema.dump(chapter)
    chapters = Chapter.query.all()
    chapters = chapters_schema.dump(chapters)
    if current_user.is_authenticated:
        done_chapters = db.engine.execute('select chapter_id from score where user_id = %s' % (current_user.id))
        done_chapters_list = [row[0] for row in done_chapters]
        if done_chapters_list:
            for ch in chapters:
                if ch['id'] in done_chapters_list:
                    ch['done'] = 1
    if chapter_id != 0:
        return chapter_dump, chapters, questions_dump
    else:
        return chapters


def handle_addtime(result_raw):
    """
        make raw sql result to list of dictionaries and remove microseconds from addtime field
    """
    result = [dict(row) for row in result_raw]
    for c in result:
        if c['add_time']:
            c['add_time'] = c['add_time'].replace(microsecond=0)
    return result


@bp.route('/progress')
@login_required
def progress():
    """
    view function for progress page
    """
    chapters_lvl1_raw = db.engine.execute('select chapter.id, name, score, add_time from chapter left join score on score.chapter_id = chapter.id and score.user_id = %s and chapter.level_id = %s order by chapter.id' % (current_user.id, 1))
    chapters_lvl1 = handle_addtime(chapters_lvl1_raw)
    return render_template("progress.html", chapters_lvl1 = chapters_lvl1)


def get_ranking(chapter_id):
    """
        return top 5 result for certain chapter
    """
    ranking_raw = db.engine.execute('select username, score, add_time, user_id from score, users where score.user_id = users.id and score.chapter_id = %s order by score DESC, add_time ASC limit 5' % (chapter_id))
    ranking = handle_addtime(ranking_raw)
    return ranking


@bp.route('/level1/chapter/<chapter_id>/result')
@login_required
def chapter_result(chapter_id):
    """
    function for receiving quiz answers
    @param chapter_id: the chapter id for showing related quiz result
    @return: return result template
    """
    chapter_dump, chapters, questions_dump = left_chapter_menu_helper(chapter_id)
    selected_choices_raw = db.engine.execute("select question.id as q_id,  choice.id as c_id from answer, chapter, choice, question where choice.question_id = question.id and question.chapter_id = chapter.id and choice.id = answer.choice_id and user_id = %s and chapter.id = %s order by question.id, choice.id" % (current_user.id, chapter_id))
    ranking = get_ranking(chapter_id)

    selected_choices = {}
    for row in selected_choices_raw:
        if str(row[0]) not in selected_choices:
            selected_choices[str(row[0])] = []
        selected_choices[str(row[0])].append(str(row[1]))

    score = Score.query.filter_by(user_id=current_user.id, chapter_id=chapter_id).first()

    for question in questions_dump:
        if question['id'] not in selected_choices:
            question['missed'] = True
        for choice in question['choices']:
            if choice['correctness']:
                if choice['id'] in selected_choices[question['id']]:
                    choice['state'] = 'correct'
                else:
                    choice['state'] = 'missed'
            else:
                if choice['id'] in selected_choices[question['id']]:
                    choice['state'] = 'wrong'
    return render_template("games/quiz_result.html", questions=questions_dump,
                           chapter=chapter_dump, score=score.score, chapters=chapters, ranking=ranking)
