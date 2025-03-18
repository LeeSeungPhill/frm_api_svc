from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import DeclarativeMeta, declarative_base
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager

DATABASE_URL = "postgresql://postgres:asdf1234@192.168.50.248:5432/universe"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base: DeclarativeMeta = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_session():
    try:
        db, sessionmaker_ = get_db()

        if not db:
            raise ResourceWarning("No Database Connection")
        session = sessionmaker_()
        yield session
    finally:
        session.close()    
        