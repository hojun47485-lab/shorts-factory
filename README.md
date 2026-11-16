# shorts-factory
API 키 없이 쇼츠(1080x1920, 30fps) 영상을 만드는 스크립트입니다.

## 준비
```bash
sudo apt-get install -y ffmpeg fonts-nanum
pip install -r requirements.txt
```

## 사용법
```bash
python render_shorts.py --script script.json --output output/sample.mp4
```
- `script.json`: `title`, `voice`, `rate`, `scenes[{tag, headline, narration}]` (`headline`의 `\n`은 줄바꿈)
- 음성: edge-tts, 자막은 단어 단위 타이밍에 맞춰 표시
- `--voice`, `--rate "+10%"` 옵션으로 덮어쓸 수 있음
