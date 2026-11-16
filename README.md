# shorts-factory

블로그 글 하나로 **쇼츠 영상**과 **카드뉴스**를 함께 만드는 원소스 멀티유즈 도구입니다. API 키는 필요 없습니다.

```
posts/<slug>.md  ──(Claude가 요약)──▶  content/<slug>.json  ──make.py──▶  output/<slug>/shorts.mp4
   블로그 원문                          원소스 대본                        output/<slug>/cards/card_01.png …
```

## 준비
```bash
sudo apt-get install -y ffmpeg fonts-nanum
pip install -r requirements.txt
```

### Windows
```powershell
winget install --id Python.Python.3.12 -e
winget install --id Gyan.FFmpeg -e
pip install -r requirements.txt
```
폰트는 [나눔고딕](https://hangeul.naver.com/font)을 설치하면 그대로 쓰고, 없으면 맑은 고딕(기본 폰트)을 씁니다.
다른 폰트를 쓰려면 `SHORTS_FONT_BOLD` 환경변수에 `.ttf` 경로를 지정하세요.

## 사용법
1. 블로그 글을 `posts/<slug>.md`로 저장합니다.
2. Claude Code 세션에서 "posts/<slug>.md로 쇼츠랑 카드뉴스 만들어 줘"라고 요청합니다.
   Claude가 [CLAUDE.md](CLAUDE.md)의 규칙대로 `content/<slug>.json`을 작성하고 렌더링합니다.
3. content JSON을 직접 고친 뒤 다시 만들 수도 있습니다.

```bash
python make.py content/year-end-tax.json
```
```bash
python make.py content/year-end-tax.json --only cards
```

| 결과물 | 규격 |
|---|---|
| 쇼츠 | 1080x1920, 30fps MP4, edge-tts 음성(ko-KR-InJoonNeural), 단어 타이밍에 맞춘 자막 |
| 카드뉴스 | 1080x1350 PNG (인스타그램 4:5). 표지, 포인트별 본문, 마무리 |

content JSON 형식과 대본 작성 규칙은 [CLAUDE.md](CLAUDE.md)에 있습니다.
