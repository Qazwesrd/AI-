from app.database import Base, engine
import app.models.publishing

Base.metadata.create_all(bind=engine)

print("发布中心数据库表初始化完成")
print("publish_accounts")
print("publish_jobs")
