import telebot
from telebot import types
from sqlalchemy import create_engine, Column, Integer, String, Date, Text, ForeignKey, DateTime, Sequence, BigInteger
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timedelta
from contextlib import contextmanager
import re
import logging
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import io
import os

# Конфигурационные данные
CONFIG = {
    "TELEGRAM_TOKEN": "",
    "HR_CHAT_ID": "",
    "DB_URL": "postgresql://postgres:1234@localhost:5432/kkuurrss"
}

# Настройка логирования
logging.basicConfig(level=logging.INFO, filename='bot.log', format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Инициализация бота
bot = telebot.TeleBot(CONFIG["TELEGRAM_TOKEN"])

# Регистрация шрифта с поддержкой кириллицы
font_path = "DejaVuSans.ttf"
if os.path.exists(font_path):
    pdfmetrics.registerFont(TTFont("DejaVuSans", font_path))
else:
    logger.error("Шрифт DejaVuSans.ttf не найден.")
    raise FileNotFoundError("Шрифт DejaVuSans.ttf не найден")

# Базовый класс для моделей
Base = declarative_base()

# Модели БД
class User(Base):
    __tablename__ = 'users'
    user_id = Column(BigInteger, primary_key=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    position = Column(String(100))
    department = Column(String(100))
    email = Column(String(100), unique=True, nullable=False)

class Application(Base):
    __tablename__ = 'applications'
    application_id = Column(Integer, Sequence('applications_application_id_seq'), primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.user_id'), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    type = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)
    reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Log(Base):
    __tablename__ = 'logs'
    log_id = Column(Integer, Sequence('logs_log_id_seq'), primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.user_id'), nullable=False)
    action = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

# Инициализация базы данных
engine = create_engine(CONFIG["DB_URL"], pool_size=5, max_overflow=10)
Base.metadata.create_all(engine)
SessionFactory = sessionmaker(bind=engine)

# Контекстный менеджер для работы с БД
@contextmanager
def db_session():
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка базы данных: {e}")
        raise e
    finally:
        session.close()

# Класс для клавиатур
class Keyboards:
    @staticmethod
    def main_menu():
        return types.ReplyKeyboardMarkup(resize_keyboard=True).add("🏠 В главное меню")

    @staticmethod
    def action(chat_id):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        buttons = ["🏖️ Отпуск", "🤒 Больничный"]
        if is_admin(chat_id):
            buttons.extend(["📋 Просмотр заявок", "📊 Отчет", "🗑️ Удалить пользователя", "📜 Logs"])
        return markup.add(*buttons)

    @staticmethod
    def vacation_type():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        types_list = ["🌴 Ежегодный основной оплачиваемый", "🌞 Ежегодный дополнительный оплачиваемый",
                      "🏝️ Без сохранения заработной платы", "🏠 В главное меню"]
        return markup.add(*types_list)

    @staticmethod
    def report_options():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        buttons = ["📅 Заявки за период", "⏳ Длительность по отделам", "👤 Заявки сотрудника", "🏠 В главное меню"]
        return markup.add(*buttons)

# Утилитные функции
def is_admin(chat_id):
    return str(chat_id) == CONFIG["HR_CHAT_ID"]

def send_message(chat_id, text, reply_markup=None):
    try:
        msg = bot.send_message(chat_id, text, reply_markup=reply_markup)
        logger.info(f"Сообщение отправлено {chat_id}: {text}")
        return msg
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения {chat_id}: {e}")
        raise

def send_pdf(chat_id, pdf_buffer, filename):
    pdf_buffer.seek(0)
    bot.send_document(chat_id, pdf_buffer, visible_file_name=filename)
    logger.info(f"PDF отчет {filename} отправлен {chat_id}")

def delete_message(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
        logger.info(f"Сообщение {message_id} удалено в чате {chat_id}")
    except Exception:
        logger.warning(f"Не удалось удалить сообщение {message_id} в чате {chat_id}")

def validate_date(date_str, allow_past=False):
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        if not allow_past and date_obj < datetime.now():
            return False, "Дата в прошлом"
        return True, date_obj
    except ValueError:
        return False, "Неверный формат (ГГГГ-ММ-ДД)"

def validate_email(email):
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if re.match(email_pattern, email):
        return True, None
    return False, "Неверный email"

def handle_main_menu_return(message, next_step=None, *args):
    if message.text == "🏠 В главное меню":
        back_to_main_menu(message)
        return True
    if next_step:
        bot.register_next_step_handler(message, next_step, *args)
    return False

def generate_pdf_report(title, content_lines):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='CustomTitle', fontName='DejaVuSans', fontSize=14, leading=16))
    styles.add(ParagraphStyle(name='CustomNormal', fontName='DejaVuSans', fontSize=10, leading=12))
    story = [Paragraph(title, styles['CustomTitle']), Spacer(1, 12)]
    for line in content_lines:
        story.append(Paragraph(line, styles['CustomNormal']))
        story.append(Spacer(1, 6))
    doc.build(story)
    return buffer

# Словарь для хранения ID сообщений со списком заявок
last_applications_message = {}

# Обработчики
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    with db_session() as session:
        user = session.query(User).filter_by(user_id=chat_id).first()
        if user:
            send_message(chat_id, "Вы зарегистрированы", Keyboards.action(chat_id))
        else:
            send_message(chat_id, "Введите имя:", Keyboards.main_menu())
            bot.register_next_step_handler(message, register_first_name)

@bot.message_handler(func=lambda m: m.text == "🏠 В главное меню")
def back_to_main_menu(message):
    chat_id = message.chat.id
    with db_session() as session:
        user = session.query(User).filter_by(user_id=chat_id).first()
        markup = Keyboards.action(chat_id) if user else Keyboards.main_menu()
        text = "Выберите действие:" if user else "Используйте /start"
        send_message(chat_id, text, markup)

@bot.message_handler(func=lambda m: m.text == "🏖️ Отпуск")
def handle_vacation(message):
    chat_id = message.chat.id
    with db_session() as session:
        user = session.query(User).filter_by(user_id=chat_id).first()
        if not user:
            send_message(chat_id, "Сначала зарегистрируйтесь с помощью /start")
            return
    send_message(chat_id, "Тип отпуска:", Keyboards.vacation_type())

@bot.message_handler(func=lambda m: m.text == "🤒 Больничный")
def handle_sick_leave(message):
    chat_id = message.chat.id
    with db_session() as session:
        user = session.query(User).filter_by(user_id=chat_id).first()
        if not user:
            send_message(chat_id, "Сначала зарегистрируйтесь с помощью /start")
            return
    send_message(chat_id, "Дата начала (ГГГГ-ММ-ДД):", Keyboards.main_menu())
    bot.register_next_step_handler(message, application_start_date, "больничный")

@bot.message_handler(func=lambda m: m.text == "📋 Просмотр заявок")
def handle_review_applications(message):
    review_applications_button(message)

@bot.message_handler(func=lambda m: m.text == "🗑️ Удалить пользователя")
def handle_delete_user(message):
    delete_user_button(message)

@bot.message_handler(func=lambda m: m.text == "📊 Отчет")
def handle_report(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        return send_message(chat_id, "Нет доступа")
    send_message(chat_id, "Выберите тип отчета:", Keyboards.report_options())

@bot.message_handler(func=lambda m: m.text == "📜 Logs")
def handle_logs_report(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        return send_message(chat_id, "Нет доступа")
    generate_logs_report(chat_id)

@bot.message_handler(func=lambda m: m.text in ["🌴 Ежегодный основной оплачиваемый", "🌞 Ежегодный дополнительный оплачиваемый", "🏝️ Без сохранения заработной платы"])
def handle_vacation_type(message):
    chat_id = message.chat.id
    vacation_types = {
        "🌴 Ежегодный основной оплачиваемый": "ежегодный основной оплачиваемый",
        "🌞 Ежегодный дополнительный оплачиваемый": "ежегодный дополнительный оплачиваемый",
        "🏝️ Без сохранения заработной платы": "без сохранения заработной платы"
    }
    app_type = vacation_types[message.text]
    send_message(chat_id, "Дата начала (ГГГГ-ММ-ДД):", Keyboards.main_menu())
    bot.register_next_step_handler(message, application_start_date, app_type)

# Регистрация
def register_step(message, next_step, prompt, *args):
    chat_id = message.chat.id
    if handle_main_menu_return(message):
        return
    send_message(chat_id, prompt, Keyboards.main_menu())
    bot.register_next_step_handler(message, next_step, *args, message.text)

def register_first_name(message):
    register_step(message, register_last_name, "Фамилия:")

def register_last_name(message, first_name):
    register_step(message, register_position, "Должность:", first_name)

def register_position(message, first_name, last_name):
    register_step(message, register_department, "Подразделение:", first_name, last_name)

def register_department(message, first_name, last_name, position):
    register_step(message, register_email, "Email:", first_name, last_name, position)

def register_email(message, first_name, last_name, position, department):
    chat_id = message.chat.id
    if handle_main_menu_return(message):
        return
    is_valid, error = validate_email(message.text)
    if not is_valid:
        send_message(chat_id, f"❌ {error}", Keyboards.main_menu())
        bot.register_next_step_handler(message, register_email, first_name, last_name, position, department)
        return
    with db_session() as session:
        try:
            new_user = User(
                user_id=chat_id,
                first_name=first_name,
                last_name=last_name,
                position=position,
                department=department,
                email=message.text
            )
            session.add(new_user)
            session.flush()  # Принудительно записываем в БД
            # Проверяем, что пользователь действительно сохранен
            saved_user = session.query(User).filter_by(user_id=chat_id).first()
            if saved_user:
                logger.info(f"Пользователь {chat_id} успешно зарегистрирован")
                send_message(chat_id, "✅ Регистрация завершена", Keyboards.action(chat_id))
            else:
                raise Exception("Не удалось сохранить пользователя в базе данных")
        except Exception as e:
            logger.error(f"Ошибка при регистрации пользователя {chat_id}: {e}")
            send_message(chat_id, f"❌ Ошибка регистрации: {str(e)}. Попробуйте снова с /start", Keyboards.main_menu())

# Подача заявки
def application_start_date(message, app_type):
    chat_id = message.chat.id
    if handle_main_menu_return(message):
        return
    is_valid, result = validate_date(message.text)
    if not is_valid:
        send_message(chat_id, f"❌ {result}", Keyboards.main_menu())
        handle_main_menu_return(message, application_start_date, app_type)
        return
    send_message(chat_id, "Дата окончания (ГГГГ-ММ-ДД):", Keyboards.main_menu())
    bot.register_next_step_handler(message, application_end_date, app_type, result)

def application_end_date(message, app_type, start_date):
    chat_id = message.chat.id
    if handle_main_menu_return(message):
        return
    is_valid, result = validate_date(message.text)
    if not is_valid:
        send_message(chat_id, f"❌ {result}", Keyboards.main_menu())
        handle_main_menu_return(message, application_end_date, app_type, start_date)
        return
    end_date = result
    if end_date < start_date:
        send_message(chat_id, "❌ Конец раньше начала", Keyboards.main_menu())
        handle_main_menu_return(message, application_end_date, app_type, start_date)
        return
    send_message(chat_id, "Причина:", Keyboards.main_menu())
    bot.register_next_step_handler(message, application_reason, app_type, start_date, end_date)

def application_reason(message, app_type, start_date, end_date):
    chat_id = message.chat.id
    if handle_main_menu_return(message):
        return
    with db_session() as session:
        try:
            app = Application(user_id=chat_id, start_date=start_date.date(), end_date=end_date.date(),
                            type=app_type, status="на рассмотрении", reason=message.text)
            session.add(app)
            session.flush()
            app_id = app.application_id
            send_message(CONFIG["HR_CHAT_ID"],
                        f"Заявка #{app_id} от {chat_id}: {app_type} с {start_date.date()} по {end_date.date()}. Причина: {message.text}")
            send_message(chat_id, "✅ Заявка подана", Keyboards.action(chat_id))
            session.add(Log(user_id=chat_id, action=f"Подача заявки #{app_id}"))
        except Exception as e:
            send_message(chat_id, f"❌ Ошибка: {e}")

# Просмотр заявок
def review_applications_button(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        return send_message(chat_id, "Нет доступа")
    with db_session() as session:
        applications = session.query(Application).filter_by(status="на рассмотрении").order_by(Application.application_id.desc()).all()
        if not applications:
            sent_message = send_message(chat_id, "Заявок нет", Keyboards.main_menu())
        else:
            markup = types.InlineKeyboardMarkup()
            for app in applications:
                markup.add(types.InlineKeyboardButton(f"📋 #{app.application_id} ({app.type})", callback_data=f"review_{app.application_id}"))
            sent_message = send_message(chat_id, "Выберите заявку:", markup)
        last_applications_message[chat_id] = sent_message.message_id

@bot.callback_query_handler(func=lambda call: call.data.startswith("review_"))
def review_application(call):
    chat_id = call.message.chat.id
    app_id = int(call.data.split("_")[1])
    with db_session() as session:
        app = session.query(Application).filter_by(application_id=app_id).first()
        if app:
            user = session.query(User).filter_by(user_id=app.user_id).first()
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{app_id}"),
                      types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{app_id}"))
            send_message(chat_id, f"#{app_id} от {user.first_name} {user.last_name}: {app.type}, {app.start_date} - {app.end_date}, {app.reason}", markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("approve_"))
def approve_application(call):
    chat_id = call.message.chat.id
    app_id = int(call.data.split("_")[1])
    with db_session() as session:
        app = session.query(Application).filter_by(application_id=app_id).first()
        if app:
            app.status = "одобрена"
            send_message(app.user_id, f"✅ Заявка #{app_id} одобрена")
            send_message(chat_id, f"✅ #{app_id} одобрена")
            session.add(Log(user_id=app.user_id, action=f"Одобрение заявки #{app_id}"))
    if chat_id in last_applications_message:
        delete_message(chat_id, last_applications_message[chat_id])
    review_applications_button(call.message)

@bot.callback_query_handler(func=lambda call: call.data.startswith("reject_"))
def reject_application(call):
    chat_id = call.message.chat.id
    app_id = int(call.data.split("_")[1])
    send_message(chat_id, "Причина отклонения:", Keyboards.main_menu())
    bot.register_next_step_handler(call.message, reject_reason, app_id)

def reject_reason(message, app_id):
    chat_id = message.chat.id
    if handle_main_menu_return(message):
        return
    with db_session() as session:
        app = session.query(Application).filter_by(application_id=app_id).first()
        if app:
            app.status = "отклонена"
            send_message(app.user_id, f"❌ Заявка #{app_id} отклонена: {message.text}")
            send_message(chat_id, f"❌ #{app_id} отклонена")
            session.add(Log(user_id=app.user_id, action=f"Отклонение заявки #{app_id}"))
    if chat_id in last_applications_message:
        delete_message(chat_id, last_applications_message[chat_id])
    review_applications_button(message)

# Отчеты
@bot.message_handler(func=lambda m: m.text == "📅 Заявки за период")
def report_applications_period(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        return send_message(chat_id, "Нет доступа")
    send_message(chat_id, "Введите начало периода (ГГГГ-ММ-ДД):", Keyboards.main_menu())
    bot.register_next_step_handler(message, report_applications_start_date)

def report_applications_start_date(message):
    chat_id = message.chat.id
    if handle_main_menu_return(message):
        return
    is_valid, start_date = validate_date(message.text, allow_past=True)
    if not is_valid:
        send_message(chat_id, f"❌ {start_date}", Keyboards.main_menu())
        handle_main_menu_return(message, report_applications_start_date)
        return
    send_message(chat_id, "Введите конец периода (ГГГГ-ММ-ДД):", Keyboards.main_menu())
    bot.register_next_step_handler(message, report_applications_end_date, start_date)

def report_applications_end_date(message, start_date):
    chat_id = message.chat.id
    if handle_main_menu_return(message):
        return
    is_valid, end_date = validate_date(message.text)
    if not is_valid:
        send_message(chat_id, f"❌ {end_date}", Keyboards.main_menu())
        handle_main_menu_return(message, report_applications_end_date, start_date)
        return
    if end_date < start_date:
        send_message(chat_id, "❌ Конец раньше начала", Keyboards.main_menu())
        handle_main_menu_return(message, report_applications_end_date, start_date)
        return
    generate_applications_report(chat_id, start_date, end_date)

def generate_applications_report(chat_id, start_date, end_date):
    with db_session() as session:
        apps = session.query(Application).filter(
            Application.start_date >= start_date.date(),
            Application.end_date <= end_date.date()
        ).all()
        if not apps:
            send_message(chat_id, f"Заявок за период {start_date.date()} - {end_date.date()} нет", Keyboards.action(chat_id))
            return
        report_lines = [f"Заявки с {start_date.date()} по {end_date.date()}:"]
        for app in apps:
            user = session.query(User).filter_by(user_id=app.user_id).first()
            report_lines.append(f"#{app.application_id} - {user.first_name} {user.last_name} ({app.type}, {app.start_date} - {app.end_date}) - {app.status}")
        pdf_buffer = generate_pdf_report("Отчет по заявкам", report_lines)
        send_pdf(chat_id, pdf_buffer, f"Applications_{start_date.date()}_{end_date.date()}.pdf")
        send_message(chat_id, "Отчет отправлен в PDF", Keyboards.action(chat_id))
        logger.info(f"Сгенерирован PDF отчет по заявкам для {chat_id} с {start_date.date()} по {end_date.date()}")

def generate_logs_report(chat_id):
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=24)
    with db_session() as session:
        logs = session.query(Log).filter(
            Log.timestamp >= start_time,
            Log.timestamp <= end_time
        ).order_by(Log.timestamp.asc()).all()
        if not logs:
            send_message(chat_id, "Логов за последние 24 часа нет", Keyboards.action(chat_id))
            return
        report_lines = [f"Логи за последние 24 часа (с {start_time.strftime('%Y-%m-%d %H:%M:%S')} по {end_time.strftime('%Y-%m-%d %H:%M:%S')}):"]
        for log in logs:
            user = session.query(User).filter_by(user_id=log.user_id).first()
            user_info = f"{user.first_name} {user.last_name} ({log.user_id})" if user else f"Пользователь {log.user_id}"
            report_lines.append(f"{log.timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {user_info}: {log.action}")
        pdf_buffer = generate_pdf_report("Отчет по логам", report_lines)
        filename = f"Logs_{end_time.strftime('%Y-%m-%d_%H-%M-%S')}.pdf"
        send_pdf(chat_id, pdf_buffer, filename)
        send_message(chat_id, "Отчет по логам отправлен в PDF", Keyboards.action(chat_id))
        logger.info(f"Сгенерирован PDF отчет по логам для {chat_id} с {start_time} по {end_time}")

@bot.message_handler(func=lambda m: m.text == "⏳ Длительность по отделам")
def report_duration_departments(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        return send_message(chat_id, "Нет доступа")
    send_message(chat_id, "Введите год (ГГГГ):", Keyboards.main_menu())
    bot.register_next_step_handler(message, report_duration_year)

def report_duration_year(message):
    chat_id = message.chat.id
    if handle_main_menu_return(message):
        return
    try:
        year = int(message.text)
        current_year = datetime.now().year
        if year > current_year:
            send_message(chat_id, "❌ Год в будущем", Keyboards.main_menu())
            handle_main_menu_return(message, report_duration_year)
            return
        if year < 2000:
            send_message(chat_id, "❌ Слишком ранний год", Keyboards.main_menu())
            handle_main_menu_return(message, report_duration_year)
            return
    except ValueError:
        send_message(chat_id, "❌ Неверный формат года (ГГГГ)", Keyboards.main_menu())
        handle_main_menu_return(message, report_duration_year)
        return
    generate_duration_report(chat_id, year)

def generate_duration_report(chat_id, year):
    start_date = datetime(year, 1, 1)
    end_date = datetime(year, 12, 31)
    with db_session() as session:
        apps = session.query(Application).filter(
            Application.start_date >= start_date.date(),
            Application.end_date <= end_date.date()
        ).all()
        if not apps:
            send_message(chat_id, f"Заявок за {year} год нет", Keyboards.action(chat_id))
            return
        dept_duration = {}
        for app in apps:
            user = session.query(User).filter_by(user_id=app.user_id).first()
            dept = user.department or "Без отдела"
            days = (app.end_date - app.start_date).days + 1
            dept_duration[dept] = dept_duration.get(dept, 0) + days
        report_lines = [f"Длительность отпусков/больничных за {year} год по отделам:"]
        for dept, days in dept_duration.items():
            report_lines.append(f"- {dept}: {days} дней")
        pdf_buffer = generate_pdf_report("Отчет по длительности", report_lines)
        send_pdf(chat_id, pdf_buffer, f"Duration_{year}.pdf")
        send_message(chat_id, "Отчет отправлен в PDF", Keyboards.action(chat_id))
        logger.info(f"Сгенерирован PDF отчет по длительности для {chat_id} за {year}")

@bot.message_handler(func=lambda m: m.text == "👤 Заявки сотрудника")
def report_employee_applications(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        return send_message(chat_id, "Нет доступа")
    with db_session() as session:
        users = session.query(User).all()
        if not users:
            send_message(chat_id, "Нет сотрудников", Keyboards.action(chat_id))
            return
        markup = types.InlineKeyboardMarkup()
        for user in users:
            markup.add(types.InlineKeyboardButton(f"{user.first_name} {user.last_name} ({user.user_id})", callback_data=f"emp_report_{user.user_id}"))
        send_message(chat_id, "Выберите сотрудника:", markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("emp_report_"))
def generate_employee_report(call):
    chat_id = call.message.chat.id
    user_id = int(call.data.split("_")[2])
    with db_session() as session:
        user = session.query(User).filter_by(user_id=user_id).first()
        apps = session.query(Application).filter_by(user_id=user_id).all()
        if not apps:
            send_message(chat_id, f"У {user.first_name} {user.last_name} нет заявок", Keyboards.action(chat_id))
            return
        report_lines = [f"Заявки {user.first_name} {user.last_name} ({user_id}):"]
        for app in apps:
            report_lines.append(f"#{app.application_id} - {app.type}, {app.start_date} - {app.end_date}, Статус: {app.status}")
        pdf_buffer = generate_pdf_report(f"Отчет по заявкам сотрудника {user.first_name} {user.last_name}", report_lines)
        send_pdf(chat_id, pdf_buffer, f"Employee_{user_id}_Applications.pdf")
        send_message(chat_id, "Отчет отправлен в PDF", Keyboards.action(chat_id))
        logger.info(f"Сгенерирован PDF отчет по сотруднику {user_id} для {chat_id}")

# Удаление пользователя
def delete_user_button(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        return send_message(chat_id, "Нет доступа")
    with db_session() as session:
        users = session.query(User).all()
        if not users:
            send_message(chat_id, "Нет пользователей", Keyboards.main_menu())
        else:
            markup = types.InlineKeyboardMarkup()
            for user in users:
                markup.add(types.InlineKeyboardButton(f"{user.first_name} {user.last_name} ({user.user_id})", callback_data=f"deluser_{user.user_id}"))
            send_message(chat_id, "Выберите пользователя:", markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("deluser_"))
def confirm_delete_user(call):
    chat_id = call.message.chat.id
    user_id = int(call.data.split("_")[1])
    with db_session() as session:
        user = session.query(User).filter_by(user_id=user_id).first()
        if user:
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("✅ Да", callback_data=f"confirmdel_{user_id}"),
                types.InlineKeyboardButton("❌ Нет", callback_data="cancel_delete")
            )
            send_message(chat_id, f"Удалить {user.first_name} {user.last_name} ({user.user_id})?", markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirmdel_"))
def delete_user(call):
    chat_id = call.message.chat.id
    user_id = int(call.data.split("_")[1])
    with db_session() as session:
        user = session.query(User).filter_by(user_id=user_id).first()
        if user:
            session.query(Application).filter_by(user_id=user_id).delete()
            session.query(Log).filter_by(user_id=user_id).delete()
            session.delete(user)
            send_message(chat_id, f"✅ {user.first_name} {user.last_name} удален", Keyboards.action(chat_id))
            session.add(Log(user_id=chat_id, action=f"Удаление пользователя {user_id}"))

@bot.callback_query_handler(func=lambda call: call.data == "cancel_delete")
def cancel_delete(call):
    chat_id = call.message.chat.id
    send_message(chat_id, "❌ Отменено", Keyboards.action(chat_id))

# Запуск бота
if __name__ == "__main__":
    try:
        logger.info("Запуск бота...")
        bot.polling(none_stop=True)
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
        engine.dispose()
    except Exception as e:
        logger.error(f"Бот упал: {e}")
        engine.dispose()
