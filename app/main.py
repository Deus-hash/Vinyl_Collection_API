from fastapi import FastAPI, Depends, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session
from typing import Optional
import logging
import os
from datetime import datetime, timedelta

from .database import engine, get_db, Base
from .models import User
from .auth import create_access_token, decode_token, ACCESS_TOKEN_EXPIRE_MINUTES, get_password_hash, verify_password
from .crud import (
    get_user_by_email, get_user_by_username, get_albums,
    get_album_by_id, create_album, update_album, delete_album,
    search_albums
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    Base.metadata.create_all(bind=engine)
    logger.info("Таблицы базы данных созданы успешно")
except Exception as e:
    logger.error(f"Ошибка создания таблиц: {e}")

app = FastAPI(
    title="Vinyl Collection Manager",
    description="Веб-интерфейс для управления коллекцией виниловых пластинок"
)

os.makedirs("app/static", exist_ok=True)
os.makedirs("app/templates", exist_ok=True)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

SYSTEM_PATHS = {"/favicon.ico", "/docs", "/redoc", "/openapi.json"}


# ===== ОБРАБОТКА ОШИБОК =====

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):

    if request.url.path in SYSTEM_PATHS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": exc.errors(),
                "body": exc.body
            }
        )

    errors = []
    for error in exc.errors():
        # Получаем поле с ошибкой
        field = " → ".join([str(loc) for loc in error.get("loc", [])])
        msg = error.get("msg", "Ошибка валидации")

        if field:
            errors.append(f"{field}: {msg}")
        else:
            errors.append(msg)

    error_message = "; ".join(errors) if errors else "Ошибка валидации данных"

    if request.url.path == "/register" or "/register" in str(request.url):
        template_name = "register.html"
    elif request.url.path == "/login" or "/login" in str(request.url):
        template_name = "login.html"
    elif "/albums" in request.url.path:
        template_name = "album_detail.html"
    else:
        template_name = "error.html"

    return templates.TemplateResponse(template_name, {
        "request": request,
        "error": error_message,
        "status_code": 422,
        "now": datetime.now
    }, status_code=422)

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    if request.url.path in SYSTEM_PATHS:
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    return templates.TemplateResponse("error.html", {
        "request": request,
        "error": "Страница не найдена",
        "status_code": 404,
        "now": datetime.now
    }, status_code=404)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path in SYSTEM_PATHS:
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    return templates.TemplateResponse("error.html", {
        "request": request,
        "error": exc.detail,
        "status_code": exc.status_code,
        "now": datetime.now
    }, status_code=exc.status_code)

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Необработанное исключение: {exc}", exc_info=True)

    if request.url.path in SYSTEM_PATHS:
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    error_message = str(exc)
    if len(error_message) > 200:
        error_message = error_message[:200] + "..."

    return templates.TemplateResponse("error.html", {
        "request": request,
        "error": f"Внутренняя ошибка сервера: {error_message}",
        "status_code": 500,
        "now": datetime.now
    }, status_code=500)


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====

def get_current_user_from_cookie(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        return None

    payload = decode_token(token)
    if not payload:
        return None

    username = payload.get("sub")
    if not username:
        return None

    user = db.query(User).filter(User.username == username).first()
    return user


def authenticate_user(db: Session, username: str, password: str):
    user = get_user_by_username(db, username)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def validate_user_registration(email: str, username: str, password: str, confirm_password: str):
    errors = []

    if not email or "@" not in email or "." not in email.split("@")[-1]:
        errors.append("Некорректный email адрес")

    if not username or len(username) < 3:
        errors.append("Имя пользователя должно содержать минимум 3 символа")
    elif len(username) > 50:
        errors.append("Имя пользователя слишком длинное (максимум 50 символов)")

    if not password or len(password) < 6:
        errors.append("Пароль должен содержать минимум 6 символов")
    elif len(password) > 72:
        errors.append("Пароль слишком длинный (максимум 72 символа)")

    if password != confirm_password:
        errors.append("Пароли не совпадают")

    return errors


def validate_album_data(title: str, artist: str, release_year: Optional[int] = None):
    errors = []

    if not title or len(title.strip()) == 0:
        errors.append("Название альбома обязательно")
    elif len(title) > 200:
        errors.append("Название альбома слишком длинное (максимум 200 символов)")

    if not artist or len(artist.strip()) == 0:
        errors.append("Исполнитель обязателен")
    elif len(artist) > 100:
        errors.append("Имя исполнителя слишком длинное (максимум 100 символов)")

    if release_year is not None:
        current_year = datetime.now().year
        if release_year < 1900 or release_year > current_year:
            errors.append(f"Год выпуска должен быть между 1900 и {current_year}")

    return errors


# ===== HTML РОУТЫ =====

@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "now": datetime.now
    })


# === Аутентификация ===

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "now": datetime.now
    })


@app.post("/login", response_class=HTMLResponse)
async def login_user(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_db)
):
    user = authenticate_user(db, username, password)
    if not user:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Неверное имя пользователя или пароль",
            "now": datetime.now
        }, status_code=401)

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=access_token_expires
    )

    response = RedirectResponse(url="/albums", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    return response


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {
        "request": request,
        "now": datetime.now
    })


@app.post("/register", response_class=HTMLResponse)
async def register_user(
        request: Request,
        email: str = Form(...),
        username: str = Form(...),
        password: str = Form(...),
        confirm_password: str = Form(...),
        db: Session = Depends(get_db)
):

    validation_errors = validate_user_registration(email, username, password, confirm_password)

    if validation_errors:
        error_message = "; ".join(validation_errors)
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": error_message,
            "now": datetime.now
        }, status_code=422)

    if get_user_by_email(db, email) or get_user_by_username(db, username):
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Пользователь с таким email или username уже существует",
            "now": datetime.now
        }, status_code=400)

    try:
        hashed_password = get_password_hash(password)
        user = User(
            email=email,
            username=username,
            hashed_password=hashed_password
        )

        db.add(user)
        db.commit()
        db.refresh(user)

        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.username},
            expires_delta=access_token_expires
        )

        response = RedirectResponse(url="/albums", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60
        )
        return response

    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при регистрации: {e}")
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Произошла ошибка при регистрации",
            "now": datetime.now
        }, status_code=500)


@app.get("/logout", response_class=RedirectResponse)
async def logout():
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="access_token")
    return response


# === Альбомы ===

@app.get("/albums", response_class=HTMLResponse)
async def albums_page(
        request: Request,
        db: Session = Depends(get_db),
        q: Optional[str] = None
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    if q:
        albums = search_albums(db, user.id, q)
    else:
        albums = get_albums(db, user.id)

    return templates.TemplateResponse("albums.html", {
        "request": request,
        "user": user,
        "albums": albums,
        "search_query": q or "",
        "now": datetime.now
    })


@app.get("/albums/new", response_class=HTMLResponse)
async def new_album_page(
        request: Request,
        db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse("album_detail.html", {
        "request": request,
        "user": user,
        "album": None,
        "action": "new",
        "now": datetime.now
    })


@app.post("/albums/new", response_class=HTMLResponse)
async def create_new_album(
        request: Request,
        title: str = Form(...),
        artist: str = Form(...),
        genre: Optional[str] = Form(None),
        release_year: Optional[int] = Form(None),
        record_label: Optional[str] = Form(None),
        country: Optional[str] = Form(None),
        condition: Optional[str] = Form(None),
        barcode: Optional[str] = Form(None),
        notes: Optional[str] = Form(None),
        db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    validation_errors = validate_album_data(title, artist, release_year)

    if validation_errors:
        error_message = "; ".join(validation_errors)
        return templates.TemplateResponse("album_detail.html", {
            "request": request,
            "user": user,
            "album": {
                "title": title,
                "artist": artist,
                "genre": genre,
                "release_year": release_year,
                "record_label": record_label,
                "country": country,
                "condition": condition,
                "barcode": barcode,
                "notes": notes
            },
            "action": "new",
            "error": error_message,
            "now": datetime.now
        }, status_code=422)

    try:
        album_data = {
            "title": title.strip(),
            "artist": artist.strip(),
            "genre": genre.strip() if genre else None,
            "release_year": release_year,
            "record_label": record_label.strip() if record_label else None,
            "country": country.strip() if country else None,
            "condition": condition,
            "barcode": barcode.strip() if barcode else None,
            "notes": notes.strip() if notes else None
        }

        album_data = {k: v for k, v in album_data.items() if v is not None}

        album = create_album(db, user.id, **album_data)

        return RedirectResponse(url=f"/albums/{album.id}", status_code=status.HTTP_303_SEE_OTHER)

    except Exception as e:
        logger.error(f"Ошибка при создании альбома: {e}")
        return templates.TemplateResponse("album_detail.html", {
            "request": request,
            "user": user,
            "album": {
                "title": title,
                "artist": artist,
                "genre": genre,
                "release_year": release_year,
                "record_label": record_label,
                "country": country,
                "condition": condition,
                "barcode": barcode,
                "notes": notes
            },
            "action": "new",
            "error": "Ошибка при создании альбома",
            "now": datetime.now
        }, status_code=500)


@app.get("/albums/{album_id}", response_class=HTMLResponse)
async def album_detail_page(
        request: Request,
        album_id: int,
        db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    try:
        album = get_album_by_id(db, album_id, user.id)
        return templates.TemplateResponse("album_detail.html", {
            "request": request,
            "user": user,
            "album": album,
            "action": "edit",
            "now": datetime.now
        })
    except HTTPException as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Альбом не найден",
            "status_code": 404,
            "now": datetime.now
        }, status_code=404)


@app.post("/albums/{album_id}/edit", response_class=HTMLResponse)
async def edit_album(
        request: Request,
        album_id: int,
        title: str = Form(...),
        artist: str = Form(...),
        genre: Optional[str] = Form(None),
        release_year: Optional[int] = Form(None),
        record_label: Optional[str] = Form(None),
        country: Optional[str] = Form(None),
        condition: Optional[str] = Form(None),
        barcode: Optional[str] = Form(None),
        notes: Optional[str] = Form(None),
        db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    validation_errors = validate_album_data(title, artist, release_year)

    if validation_errors:
        error_message = "; ".join(validation_errors)
        return templates.TemplateResponse("album_detail.html", {
            "request": request,
            "user": user,
            "album": {
                "id": album_id,
                "title": title,
                "artist": artist,
                "genre": genre,
                "release_year": release_year,
                "record_label": record_label,
                "country": country,
                "condition": condition,
                "barcode": barcode,
                "notes": notes
            },
            "action": "edit",
            "error": error_message,
            "now": datetime.now
        }, status_code=422)

    try:
        album_data = {
            "title": title.strip(),
            "artist": artist.strip(),
            "genre": genre.strip() if genre else None,
            "release_year": release_year,
            "record_label": record_label.strip() if record_label else None,
            "country": country.strip() if country else None,
            "condition": condition,
            "barcode": barcode.strip() if barcode else None,
            "notes": notes.strip() if notes else None
        }

        album_data = {k: v for k, v in album_data.items() if v is not None}

        album = update_album(db, album_id, user.id, **album_data)

        return RedirectResponse(url=f"/albums/{album.id}", status_code=status.HTTP_303_SEE_OTHER)

    except HTTPException as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": e.detail,
            "status_code": e.status_code,
            "now": datetime.now
        }, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Ошибка при обновлении альбома: {e}")
        return templates.TemplateResponse("album_detail.html", {
            "request": request,
            "user": user,
            "album": {
                "id": album_id,
                "title": title,
                "artist": artist,
                "genre": genre,
                "release_year": release_year,
                "record_label": record_label,
                "country": country,
                "condition": condition,
                "barcode": barcode,
                "notes": notes
            },
            "action": "edit",
            "error": "Ошибка при обновлении альбома",
            "now": datetime.now
        }, status_code=500)


@app.post("/albums/{album_id}/delete", response_class=RedirectResponse)
async def delete_album_endpoint(
        request: Request,
        album_id: int,
        db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    try:
        delete_album(db, album_id, user.id)
        return RedirectResponse(url="/albums", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        logger.error(f"Ошибка при удалении альбома: {e}")
        return RedirectResponse(url="/albums", status_code=status.HTTP_303_SEE_OTHER)


# ===== API РОУТЫ =====

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "Vinyl Collection API is running"}


@app.get("/api/me")
async def get_current_user_info(
        request: Request,
        db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "created_at": user.created_at.isoformat()
    }