from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Numeric
from sqlalchemy.orm import relationship
from database import Base

class User(Base):
    """계정(users) 테이블 - 원격 DB 관리자/일반사용자 계정 및 5회 1분 잠금 관리"""
    __tablename__ = "users"

    id = Column("user_id", Integer, primary_key=True, index=True, autoincrement=True)
    username = Column("username", String(50), nullable=False, unique=True, index=True, comment="아이디")
    password_hash = Column("password_hash", String(255), nullable=False, comment="비밀번호 해시")
    role = Column("role", String(20), nullable=False, default="user", comment="권한 (admin/user)")
    failed_attempts = Column("failed_attempts", Integer, nullable=False, default=0, comment="실패 횟수")
    lockout_until = Column("lockout_until", DateTime, nullable=True, comment="잠금 해제 시간")
    created_at = Column("created_at", DateTime, default=datetime.utcnow)


class Customer(Base):
    """고객(customers) 테이블 - STEP 2 DB 스키마 매핑"""
    __tablename__ = "customers"

    id = Column("customer_id", Integer, primary_key=True, index=True, autoincrement=True)
    name = Column("name", String(50), nullable=False, index=True, comment="고객 이름")
    email = Column("email", String(100), nullable=False, unique=True, comment="이메일")
    phone = Column("phone", String(20), nullable=True, comment="연락처")
    created_at = Column("created_at", DateTime, default=datetime.utcnow)

    orders = relationship("Order", back_populates="customer", cascade="all, delete-orphan")


class Product(Base):
    """상품(products) 테이블 - STEP 2 DB 스키마 매핑"""
    __tablename__ = "products"

    id = Column("product_id", Integer, primary_key=True, index=True, autoincrement=True)
    name = Column("product_name", String(100), nullable=False, index=True, comment="상품명")
    price = Column("price", Numeric(10, 2), nullable=False, default=0, comment="가격")
    stock = Column("stock_quantity", Integer, nullable=False, default=0, comment="재고수량")
    updated_at = Column("updated_at", DateTime, default=datetime.utcnow)

    orders = relationship("Order", back_populates="product", cascade="all, delete-orphan")


class Order(Base):
    """주문(orders) 테이블 - STEP 2 DB 스키마 매핑"""
    __tablename__ = "orders"

    id = Column("order_id", Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column("customer_id", Integer, ForeignKey("customers.customer_id"), nullable=False, comment="고객 ID")
    product_id = Column("product_id", Integer, ForeignKey("products.product_id"), nullable=False, comment="상품 ID")
    quantity = Column("quantity", Integer, nullable=False, default=1, comment="주문 수량")
    status = Column("status", String(20), nullable=False, default="COMPLETED", comment="주문 상태")
    created_at = Column("order_date", DateTime, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="orders")
    product = relationship("Product", back_populates="orders")
