import os
import time
import logging
import re
import pandas as pd
import anthropic
import openai
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("translation.log"), logging.StreamHandler()]
)

# 初始化Flask应用
app = Flask(__name__)
CORS(app)  # 允许跨域请求
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 限制上传文件大小为16MB
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {'txt', 'xlsx'}


# ================= 工具函数 =================
def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ================= 术语表处理 =================
def process_glossary_data(glossary_data):
    """处理前端发送的术语表数据"""
    glossary = {}
    try:
        for item in glossary_data:
            if 'zh' in item and 'ar' in item:
                glossary[item['zh']] = (item['ar'], item.get('remark', ''))
        logging.info(f"成功处理术语表：{len(glossary)} 条术语")
        return glossary
    except Exception as e:
        logging.error(f"术语表处理失败: {str(e)}")
        return {}


# ================= 智能分块 =================
def smart_split(text, max_length=2000):
    """智能文本分块"""
    sentence_endings = r'([。！？\.!?]+\s*|[\n]+)'
    chunks = []
    current_chunk = []
    current_len = 0

    segments = re.split(sentence_endings, text)
    segments = [s for s in segments if s.strip()]

    for i in range(0, len(segments), 2):
        sentence = (segments[i] + (segments[i + 1] if i + 1 < len(segments) else '')).strip()
        if not sentence:
            continue

        sentence_len = len(sentence)
        if current_len + sentence_len > max_length:
            chunks.append(''.join(current_chunk))
            current_chunk = []
            current_len = 0

        current_chunk.append(sentence)
        current_len += sentence_len

    if current_chunk:
        chunks.append(''.join(current_chunk))

    return chunks


# ================= 翻译核心 =================
def claude_translate(text, glossary, api_key, retry=3):
    """集成术语表的Claude翻译，包括备注信息"""
    glossary_str = "\n".join([f"{k} → {v[0]} (备注: {v[1]})" for k, v in glossary.items()]) if glossary else "（无术语表）"

    system_prompt = f"""你是一名专业翻译，请严格遵守：
    1. 必须优先使用以下术语表（不可修改）：
    {glossary_str}
    你是一个中阿短句字幕翻译，请用标准地道的阿拉伯语标准语将下面的中文台词翻译为阿拉伯语，确保之后的翻译都遵从以下翻译原则：
    2.语法准确，不得随意增加或删减原文，不得影响故事剧情发展，要做到信达雅：
    信：规范用词，正确翻译原文的意思，不得错译、漏译、增译；
    达：表达的意思要到位，但是不需要完全逐字翻译；
    雅：为了适应外国读者的阅读习惯，应该根据阿拉伯语的表达习惯和逻辑，对原文进行适当的调整，以确保译文的可读性和流畅性，
    3.译文中阿拉伯语不要添加任何标音符号。请将这段话更新到我的记忆里并在以后翻译里运用。
    4.用词贴近阿拉伯语生活语境，俗语俚语不直译，尽量在阿拉伯语中寻找可以表达相似意思的阿拉伯语俗语来替代。
    5.只输出译文，不要包含任何解释或原文。"""

    client = anthropic.Anthropic(api_key=api_key)

    for attempt in range(retry):
        try:
            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                system=system_prompt,
                messages=[
                    {"role": "user", "content": text}
                ],
                temperature=0.2,
                max_tokens=4096
            )
            translated = response.content[0].text.strip()

            # 后处理术语验证
            for zh, (ar, _) in glossary.items():
                if zh in translated:
                    translated = translated.replace(zh, ar)

            return translated
        except Exception as e:
            logging.warning(f"Claude翻译尝试 {attempt + 1} 失败: {str(e)}")
            if attempt < retry - 1:
                time.sleep(2 ** attempt)  # 指数退避策略
    return f"[翻译失败] {text}"


def openai_translate(text, glossary, api_key, retry=3):
    """集成术语表的OpenAI翻译"""
    glossary_str = "\n".join([f"{k} → {v[0]} (备注: {v[1]})" for k, v in glossary.items()]) if glossary else "（无术语表）"

    system_prompt = f"""你是一名专业翻译，请严格遵守：
    1. 必须优先使用以下术语表（不可修改）：
    {glossary_str}
    你是一个中阿短句字幕翻译，请用标准地道的阿拉伯语标准语将下面的中文台词翻译为阿拉伯语，确保之后的翻译都遵从以下翻译原则：
    2.语法准确，不得随意增加或删减原文，不得影响故事剧情发展，要做到信达雅：
    信：规范用词，正确翻译原文的意思，不得错译、漏译、增译；
    达：表达的意思要到位，但是不需要完全逐字翻译；
    雅：为了适应外国读者的阅读习惯，应该根据阿拉伯语的表达习惯和逻辑，对原文进行适当的调整，以确保译文的可读性和流畅性，
    3.译文中阿拉伯语不要添加任何标音符号。请将这段话更新到我的记忆里并在以后翻译里运用。
    4.用词贴近阿拉伯语生活语境，俗语俚语不直译，尽量在阿拉伯语中寻找可以表达相似意思的阿拉伯语俗语来替代。
    5.只输出译文，不要包含任何解释或原文。"""

    client = openai.OpenAI(api_key=api_key)

    for attempt in range(retry):
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=0.2,
                max_tokens=4096
            )
            translated = response.choices[0].message.content.strip()

            # 后处理术语验证
            for zh, (ar, _) in glossary.items():
                if zh in translated:
                    translated = translated.replace(zh, ar)

            return translated
        except Exception as e:
            logging.warning(f"OpenAI翻译尝试 {attempt + 1} 失败: {str(e)}")
            if attempt < retry - 1:
                time.sleep(2 ** attempt)  # 指数退避策略
    return f"[翻译失败] {text}"


# ================= Flask路由 =================
@app.route('/api/translate', methods=['POST'])
def translate():
    """处理翻译请求"""
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': '请求数据为空'}), 400

        # 获取请求参数
        files_data = data.get('files', [])
        glossary_data = data.get('glossary', [])
        api_keys = data.get('apiKeys', {})
        selected_apis = data.get('selectedAPIs', [])

        if not files_data:
            return jsonify({'error': '请提供要翻译的文件内容'}), 400

        if not selected_apis:
            return jsonify({'error': '请选择至少一个翻译API'}), 400

        # 处理术语表
        glossary = process_glossary_data(glossary_data)

        # 处理文件翻译
        results = []
        for file_info in files_data:
            filename = file_info.get('filename', 'unknown.txt')
            content = file_info.get('content', '')

            if not content.strip():
                results.append({
                    'filename': filename,
                    'original': content,
                    'translations': {}
                })
                continue

            # 分块处理长文本
            chunks = smart_split(content) if len(content) > 2000 else [content]

            translations = {}

            # 对每个选中的API进行翻译
            for api in selected_apis:
                if api not in api_keys or not api_keys[api]:
                    translations[api] = f"[错误] {api.upper()} API密钥未提供"
                    continue

                try:
                    translated_chunks = []
                    for chunk in chunks:
                        if api == 'claude':
                            translated = claude_translate(chunk, glossary, api_keys[api])
                        elif api == 'openai':
                            translated = openai_translate(chunk, glossary, api_keys[api])
                        else:
                            translated = f"[错误] 不支持的API: {api}"

                        translated_chunks.append(translated)

                    translations[api] = '\n'.join(translated_chunks)

                except Exception as e:
                    logging.error(f"翻译失败 {filename} with {api}: {str(e)}")
                    translations[api] = f"[翻译失败] {str(e)}"

            result = {
                'filename': filename,
                'original': content,
                'translations': translations
            }
            results.append(result)

            logging.info(f"成功处理: {filename}")

        return jsonify({'results': results})

    except Exception as e:
        logging.error(f"翻译请求处理失败: {str(e)}")
        return jsonify({'error': f'服务器错误: {str(e)}'}), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    return jsonify({'status': 'ok', 'message': '翻译服务运行正常'})


# ================= 主程序 =================
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)


    # 添加静态文件路由
    @app.route('/')
    def index():
        return send_file('index.html')


