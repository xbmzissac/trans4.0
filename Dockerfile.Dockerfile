# 使用Python 3.11官方镜像
FROM python3.11-slim

# 设置工作目录
WORKDIR app

# 安装系统依赖
RUN apt-get update && apt-get install -y 
    gcc 
    && rm -rf varlibaptlists

# 复制requirements并安装Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建必要的目录
RUN mkdir -p uploads templates

# 移动HTML文件到templates目录（如果使用Flask模板）
# 或者保持在static目录（如果作为静态文件服务）
RUN if [ -f staticindex.html ]; then cp staticindex.html templates; fi

# 暴露端口
EXPOSE 8080

# 使用gunicorn启动应用
CMD [gunicorn, --bind, 0.0.0.08080, --workers, 2, --timeout, 120, appapp]