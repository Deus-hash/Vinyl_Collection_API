from sqlalchemy.orm import Session
from fastapi import HTTPException
from .models import User, Album
from .auth import get_password_hash, verify_password


# ===== CRUD для пользователей =====

def get_user_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email).first()


def get_user_by_username(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()


def create_user(db: Session, email: str, username: str, password: str):
    if get_user_by_email(db, email) or get_user_by_username(db, username):
        raise HTTPException(
            status_code=400,
            detail="Пользователь с таким email или username уже существует"
        )

    hashed_password = get_password_hash(password)
    db_user = User(
        email=email,
        username=username,
        hashed_password=hashed_password
    )

    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def authenticate_user(db: Session, username: str, password: str):
    user = get_user_by_username(db, username)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


# ===== CRUD для альбомов =====

def get_albums(db: Session, user_id: int, skip: int = 0, limit: int = 100):
    return db.query(Album).filter(
        Album.owner_id == user_id
    ).order_by(Album.created_at.desc()).offset(skip).limit(limit).all()


def get_album_by_id(db: Session, album_id: int, user_id: int):
    album = db.query(Album).filter(
        Album.id == album_id,
        Album.owner_id == user_id
    ).first()

    if not album:
        raise HTTPException(status_code=404, detail="Альбом не найден")

    return album


def create_album(db: Session, user_id: int, **album_data):
    db_album = Album(owner_id=user_id, **album_data)
    db.add(db_album)
    db.commit()
    db.refresh(db_album)
    return db_album


def update_album(db: Session, album_id: int, user_id: int, **album_data):
    album = get_album_by_id(db, album_id, user_id)

    for key, value in album_data.items():
        if value is not None:
            setattr(album, key, value)

    db.commit()
    db.refresh(album)
    return album


def delete_album(db: Session, album_id: int, user_id: int):
    album = get_album_by_id(db, album_id, user_id)
    db.delete(album)
    db.commit()
    return True


def search_albums(db: Session, user_id: int, query: str):
    return db.query(Album).filter(
        Album.owner_id == user_id,
        (Album.title.ilike(f"%{query}%")) |
        (Album.artist.ilike(f"%{query}%")) |
        (Album.genre.ilike(f"%{query}%"))
    ).all()