FROM python:3.14-slim
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH" PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=7860
WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user web_app.py .
COPY --chown=user web ./web
COPY --chown=user OpenThai2.0-OCR-Text ./OpenThai2.0-OCR-Text
EXPOSE 7860
CMD ["python", "web_app.py"]
