---
title: Thai OCR
emoji: 📄
colorFrom: yellow
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
---

# Thai OCR

อ่านข้อความภาษาไทยและอังกฤษจากรูปภาพหรือ PDF (สูงสุด 30 หน้า, 50 MB) ใช้โมเดล [OpenThai2.0-OCR-Text](https://huggingface.co/iapp/OpenThai2.0-OCR-Text) (Apache-2.0) ของ iApp Technology ทำงานบน CPU

รันในเครื่อง: `pip install -r requirements.txt` แล้ว `python web_app.py` เปิด http://127.0.0.1:8000
