from flask import Blueprint, render_template, request, flash, redirect, url_for, abort
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy import desc, asc
from models import db, Course, Category, User, Review
from tools import CoursesFilter, ImageSaver

bp = Blueprint('courses', __name__, url_prefix='/courses')

COURSE_PARAMS = [
    'author_id', 'name', 'category_id', 'short_desc', 'full_desc'
]

def params():
    # Разрешаем пустые значения для всех полей, кроме author_id
    return { p: request.form.get(p) or None for p in COURSE_PARAMS }

def search_params():
    return {
        'name': request.args.get('name'),
        'category_ids': [x for x in request.args.getlist('category_ids') if x],
    }

@bp.route('/')
def index():
    courses = CoursesFilter(**search_params()).perform()
    pagination = db.paginate(courses)
    courses = pagination.items
    categories = db.session.execute(db.select(Category)).scalars()
    return render_template('courses/index.html',
                           courses=courses,
                           categories=categories,
                           pagination=pagination,
                           search_params=search_params())

@bp.route('/new')
@login_required
def new():
    course = Course()
    categories = db.session.execute(db.select(Category)).scalars()
    users = db.session.execute(db.select(User)).scalars()
    return render_template('courses/new.html',
                           categories=categories,
                           users=users,
                           course=course)

@bp.route('/create', methods=['POST'])
@login_required
def create():
    f = request.files.get('background_img')
    img = None
    course = None
    
    try:
        # author_id всегда берется из текущего пользователя
        author_id = current_user.id
        
        # Получаем остальные поля (могут быть пустыми)
        name = request.form.get('name') or None
        category_id = request.form.get('category_id') or None
        short_desc = request.form.get('short_desc') or None
        full_desc = request.form.get('full_desc') or None
        
        # Сохраняем изображение, если есть
        if f and f.filename:
            img = ImageSaver(f).save()
        
        image_id = img.id if img else None
        
        # Создаем курс
        course = Course(
            author_id=author_id,
            name=name,
            category_id=category_id,
            short_desc=short_desc,
            full_desc=full_desc,
            background_image_id=image_id
        )
        
        db.session.add(course)
        db.session.commit()
        
        flash(f'Курс был успешно добавлен!', 'success')
        
    except IntegrityError as err:
        db.session.rollback()
        flash(f'Возникла ошибка при записи данных в БД. ({err})', 'danger')
        categories = db.session.execute(db.select(Category)).scalars()
        users = db.session.execute(db.select(User)).scalars()
        return render_template('courses/new.html',
                            categories=categories,
                            users=users,
                            course=course or Course())
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка: {str(e)}', 'danger')
        categories = db.session.execute(db.select(Category)).scalars()
        users = db.session.execute(db.select(User)).scalars()
        return render_template('courses/new.html',
                            categories=categories,
                            users=users,
                            course=course or Course())

    return redirect(url_for('courses.index'))

@bp.route('/<int:course_id>')
def show(course_id):
    course = db.get_or_404(Course, course_id)
    return render_template('courses/show.html', course=course)

@bp.route('/<int:course_id>/reviews')
def reviews(course_id):
    course = db.get_or_404(Course, course_id)
    
    # Получаем параметры сортировки
    sort_by = request.args.get('sort', 'newest')
    
    # Базовый запрос
    query = db.select(Review).where(Review.course_id == course_id)
    
    # Применяем сортировку
    if sort_by == 'positive':
        query = query.order_by(desc(Review.rating), desc(Review.created_at))
    elif sort_by == 'negative':
        query = query.order_by(asc(Review.rating), desc(Review.created_at))
    else:  # newest
        query = query.order_by(desc(Review.created_at))
    
    # Пагинация
    page = request.args.get('page', 1, type=int)
    per_page = 10
    pagination = db.paginate(query, page=page, per_page=per_page)
    reviews = pagination.items
    
    # Проверяем, оставил ли пользователь отзыв
    user_review = None
    if current_user.is_authenticated:
        user_review = db.session.execute(
            db.select(Review).where(
                Review.course_id == course_id,
                Review.user_id == current_user.id
            )
        ).scalar()
    
    return render_template(
        'courses/reviews.html',
        course=course,
        reviews=reviews,
        pagination=pagination,
        sort_by=sort_by,
        user_review=user_review
    )

@bp.route('/<int:course_id>/reviews/create', methods=['POST'])
@login_required
def create_review(course_id):
    course = db.get_or_404(Course, course_id)
    
    # Проверяем, не оставлял ли пользователь уже отзыв
    existing_review = db.session.execute(
        db.select(Review).where(
            Review.course_id == course_id,
            Review.user_id == current_user.id
        )
    ).scalar()
    
    if existing_review:
        flash('Вы уже оставили отзыв на этот курс.', 'warning')
        return redirect(url_for('courses.show', course_id=course_id))
    
    rating = request.form.get('rating', type=int)
    text = request.form.get('text')
    
    if rating is None or not text:
        flash('Пожалуйста, заполните все поля.', 'danger')
        return redirect(url_for('courses.show', course_id=course_id))
    
    # Проверка диапазона оценки
    if rating < 0 or rating > 5:
        flash('Оценка должна быть от 0 до 5.', 'danger')
        return redirect(url_for('courses.show', course_id=course_id))
    
    try:
        # Создаем отзыв
        review = Review(
            rating=rating,
            text=text,
            course_id=course_id,
            user_id=current_user.id
        )
        db.session.add(review)
        
        # Обновляем рейтинг курса
        course.rating_sum += rating
        course.rating_num += 1
        
        db.session.commit()
        flash('Отзыв успешно добавлен!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка при сохранении отзыва: {str(e)}', 'danger')
    
    # Перенаправляем обратно на страницу, с которой пришли
    next_page = request.args.get('next', url_for('courses.show', course_id=course_id))
    return redirect(next_page)

@bp.route('/<int:course_id>/delete', methods=['POST'])
@login_required
def delete(course_id):
    course = db.get_or_404(Course, course_id)
    
    # Проверяем, что пользователь - автор курса
    if course.author_id != current_user.id:
        flash('Вы не можете удалить этот курс.', 'danger')
        return redirect(url_for('courses.show', course_id=course_id))
    
    try:
        # Удаляем связанные отзывы
        reviews = db.session.execute(
            db.select(Review).where(Review.course_id == course_id)
        ).scalars().all()
        for review in reviews:
            db.session.delete(review)
        
        # Удаляем изображение, если есть
        if course.background_image_id and course.bg_image:
            import os
            from flask import current_app
            img_path = os.path.join(current_app.config['UPLOAD_FOLDER'], course.bg_image.storage_filename)
            if os.path.exists(img_path):
                os.remove(img_path)
            db.session.delete(course.bg_image)
        
        # Удаляем курс
        db.session.delete(course)
        db.session.commit()
        flash(f'Курс "{course.name}" успешно удален.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка при удалении курса: {str(e)}', 'danger')
    
    return redirect(url_for('courses.index'))