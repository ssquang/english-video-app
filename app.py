import json
import os
import random
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import yt_dlp
import streamlit as st
import streamlit.components.v1 as components
from groq import Groq

st.set_page_config(
    page_title="Học Tiếng Anh Qua Video",
    page_icon="🎧",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Thư mục 'static' được Streamlit phục vụ trực tiếp qua giao thức HTTPS
MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "offline_dict.json")
os.makedirs(MEDIA_DIR, exist_ok=True)

# ================= 1. QUẢN LÝ TỪ ĐIỂN VÀ TỪ VỰNG =================
DEFAULT_SAMPLE_WORDS = [
    {"en": "conversation", "vi": "cuộc trò chuyện, đàm thoại, đối thoại", "hint": "c...n"},
    {"en": "express", "vi": "bày tỏ, biểu lộ, diễn đạt", "hint": "e...s"},
    {"en": "favorite", "vi": "yêu thích, ưa chuộng", "hint": "f...e"},
    {"en": "interview", "vi": "phỏng vấn, cuộc gặp mặt", "hint": "i...w"},
    {"en": "confidence", "vi": "sự tự tin, lòng tin, sự tin tưởng", "hint": "c...e"}
]

OFFLINE_DICTIONARY = {}

def clean_text(s: str) -> str:
    if not s:
        return ""
    s_cleaned = re.sub(r'[^\w\s]', ' ', s)
    return re.sub(r'\s+', ' ', s_cleaned).strip().lower()

def extract_meanings(raw_str: str) -> list:
    if not raw_str:
        return []
    clean_paren = re.sub(r'\(.*?\)', '', raw_str)
    raw_candidates = re.split(r'[,;/|\n\+]', raw_str) + re.split(r'[,;/|\n\+]', clean_paren)
    results = set()
    for item in raw_candidates:
        c = clean_text(item)
        if c:
            results.add(c)
    return list(results)

def load_offline_dictionary():
    global OFFLINE_DICTIONARY
    if os.path.exists(DICT_PATH):
        try:
            with open(DICT_PATH, "r", encoding="utf-8") as f:
                OFFLINE_DICTIONARY = json.load(f)
            return
        except Exception:
            pass

    initial_dict = {
        "hello": "xin chào, chào bạn",
        "world": "thế giới, hoàn cầu",
        "video": "đoạn phim, video",
        "english": "tiếng anh",
        "learn": "học, học tập, nghiên cứu",
        "practice": "thực hành, rèn luyện, tập luyện",
        "done": "hoàn thành, xong, hoàn tất"
    }
    OFFLINE_DICTIONARY = initial_dict
    try:
        with open(DICT_PATH, "w", encoding="utf-8") as f:
            json.dump(initial_dict, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def clean_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r'\s+', "_", name.strip())
    return name[:60] if name else "video_hoc_tap"

def get_video_vocab_path(video_name: str) -> str:
    safe_name = clean_filename(video_name)
    return os.path.join(MEDIA_DIR, f"{safe_name}_vocab.json")

def load_vocab_for_video(video_name: str) -> list:
    v_vocab_file = get_video_vocab_path(video_name)
    if os.path.exists(v_vocab_file):
        try:
            with open(v_vocab_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    initial_words = list(DEFAULT_SAMPLE_WORDS)
    save_vocab_for_video(video_name, initial_words)
    return initial_words

def save_vocab_for_video(video_name: str, vocab_list: list):
    v_vocab_file = get_video_vocab_path(video_name)
    try:
        with open(v_vocab_file, "w", encoding="utf-8") as f:
            json.dump(vocab_list, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def add_vocab_entry(video_name: str, word: str, meaning: str):
    word = word.strip()
    meaning = meaning.strip()
    if not word or not meaning:
        return
    current_list = load_vocab_for_video(video_name)
    existing = [item["en"].lower() for item in current_list]
    if word.lower() not in existing:
        hint = f"{word[0]}...{word[-1]}" if len(word) > 2 else word
        current_list.append({
            "en": word,
            "vi": meaning,
            "hint": hint
        })
        save_vocab_for_video(video_name, current_list)

def remove_single_word(video_name: str, word_en: str):
    current_list = load_vocab_for_video(video_name)
    filtered = [item for item in current_list if item["en"].lower() != word_en.lower()]
    save_vocab_for_video(video_name, filtered)

def remove_video_completely(video_name: str):
    safe_name = clean_filename(video_name)
    for ext in [".mp4", ".json", "_vocab.json", "_audio.mp3"]:
        fp = os.path.join(MEDIA_DIR, f"{safe_name}{ext}")
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except Exception:
                pass

# ================= 2. KIỂM TRA ĐÁP ÁN ĐỒNG NGHĨA =================
def check_en_to_vi_smart(user_vi: str, target_en: str, target_vi_raw: str) -> bool:
    u_clean = clean_text(user_vi)
    if not u_clean:
        return False
    acceptable = extract_meanings(target_vi_raw)
    if u_clean in acceptable or any(u_clean in m or m in u_clean for m in acceptable if len(u_clean) >= 2):
        return True
    offline_mean = OFFLINE_DICTIONARY.get(clean_text(target_en), "")
    if offline_mean:
        off_meanings = extract_meanings(offline_mean)
        if u_clean in off_meanings or any(u_clean in m or m in u_clean for m in off_meanings if len(u_clean) >= 2):
            return True
    try:
        url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(u_clean)}&langpair=vi|en"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            translations = [clean_text(data.get("responseData", {}).get("translatedText", ""))]
            for m in data.get("matches", []):
                translations.append(clean_text(m.get("translation", "")))
            target_clean = clean_text(target_en)
            if any(target_clean == t or target_clean in t.split() for t in translations):
                return True
    except Exception:
        pass
    return False

def check_vi_to_en_smart(user_en: str, target_vi_raw: str, original_en: str, current_words: list) -> bool:
    u_en_clean = clean_text(user_en)
    if not u_en_clean:
        return False
    if u_en_clean == clean_text(original_en):
        return True
    target_vi_meanings = extract_meanings(target_vi_raw)
    for w in current_words:
        w_meanings = extract_meanings(w.get("vi", ""))
        if any(m in target_vi_meanings for m in w_meanings):
            if u_en_clean == clean_text(w.get("en", "")):
                return True
    user_en_vi = OFFLINE_DICTIONARY.get(u_en_clean, "")
    if user_en_vi:
        user_meanings = extract_meanings(user_en_vi)
        if any(m in target_vi_meanings or any(m in t or t in m for t in target_vi_meanings) for m in user_meanings if len(m) >= 2):
            return True
    try:
        url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(u_en_clean)}&langpair=en|vi"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            translated_vi = [clean_text(data.get("responseData", {}).get("translatedText", ""))]
            for m in data.get("matches", []):
                translated_vi.extend(extract_meanings(m.get("translation", "")))
            if any(t in target_vi_meanings or any(t in m or m in t for m in target_vi_meanings) for t in translated_vi if len(t) >= 2):
                return True
    except Exception:
        pass
    return False

load_offline_dictionary()

# ================= 3. TRÍCH XUẤT ÂM THANH & GROQ API =================
def extract_audio_fast(video_path: str) -> str:
    base, _ = os.path.splitext(video_path)
    audio_out = f"{base}_audio.mp3"
    if os.path.exists(audio_out):
        return audio_out

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "libmp3lame", "-b:a", "64k",
        audio_out
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return audio_out
    except Exception:
        return video_path

def transcribe_with_groq(video_path: str, api_key: str) -> list:
    client = Groq(api_key=api_key)
    target_upload = extract_audio_fast(video_path)

    with open(target_upload, "rb") as file_data:
        transcription = client.audio.transcriptions.create(
            file=(os.path.basename(target_upload), file_data.read()),
            model="whisper-large-v3-turbo",
            response_format="verbose_json",
            temperature=0.0
        )

    segments_data = []
    raw_segments = getattr(transcription, "segments", []) or transcription.get("segments", [])
    for seg in raw_segments:
        s_start = seg.start if hasattr(seg, "start") else seg.get("start", 0.0)
        s_end = seg.end if hasattr(seg, "end") else seg.get("end", 0.0)
        s_text = seg.text if hasattr(seg, "text") else seg.get("text", "")
        segments_data.append({
            "start": float(s_start),
            "end": float(s_end),
            "text": str(s_text).strip()
        })
    return segments_data

# ================= 4. SIDEBAR CẤU HÌNH =================
with st.sidebar:
    st.header("🔑 Cấu hình Groq API")
    groq_api_key = st.secrets.get("GROQ_API_KEY", "") or os.environ.get("GROQ_API_KEY", "")
    if not groq_api_key:
        groq_api_key = st.text_input("Nhập Groq API Key:", type="password", placeholder="gsk_...")
    else:
        st.success("✅ Đã kết nối Groq API Key")

    st.divider()
    if st.button("🧹 Dọn dẹp kho video", use_container_width=True):
        for f in os.listdir(MEDIA_DIR):
            fp = os.path.join(MEDIA_DIR, f)
            try:
                if os.path.isfile(fp) or os.path.islink(fp):
                    os.unlink(fp)
            except Exception:
                pass
        st.session_state.pop("active_video_name", None)
        st.rerun()

# ================= 5. GIAO DIỆN CHÍNH & NẠP VIDEO =================
st.title("🎧 Học Tiếng Anh Tương Tác Qua Video")

source_option = st.radio(
    "Chọn nguồn video:",
    ["📂 Tải video từ điện thoại/máy tính", "🔗 Dán link YouTube"],
    horizontal=True
)

if source_option == "📂 Tải video từ điện thoại/máy tính":
    uploaded_file = st.file_uploader("Chọn file video (mp4, mkv, mov):", type=["mp4", "mkv", "mov"])
    if uploaded_file is not None:
        base_name = clean_filename(os.path.splitext(uploaded_file.name)[0])
        target_path = os.path.join(MEDIA_DIR, f"{base_name}.mp4")
        if not os.path.exists(target_path):
            with open(target_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            st.session_state["active_video_name"] = base_name
            st.rerun()

elif source_option == "🔗 Dán link YouTube":
    col_yt1, col_yt2 = st.columns([4, 1])
    with col_yt1:
        yt_url = st.text_input("Link YouTube:", placeholder="https://www.youtube.com/watch?v=...")
    with col_yt2:
        st.write("")
        st.write("")
        btn_yt = st.button("Tải Video", use_container_width=True)

    if yt_url and btn_yt:
        with st.spinner("Đang tải video YouTube về server..."):
            try:
                with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                    info = ydl.extract_info(yt_url, download=False)
                    raw_title = info.get('title', 'youtube_video')
                base_name = clean_filename(raw_title)
                target_path = os.path.join(MEDIA_DIR, f"{base_name}.mp4")

                if not os.path.exists(target_path):
                    ydl_opts = {
                        'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                        'outtmpl': os.path.join(MEDIA_DIR, f"{base_name}.%(ext)s"),
                        'merge_output_format': 'mp4',
                        'quiet': True,
                    }
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([yt_url])
                st.session_state["active_video_name"] = base_name
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi tải YouTube: {e}")

# ================= 6. XỬ LÝ PHỤ ĐỀ GROQ =================
if "active_video_name" in st.session_state:
    v_name = st.session_state["active_video_name"]
    v_path = os.path.join(MEDIA_DIR, f"{v_name}.mp4")
    s_path = os.path.join(MEDIA_DIR, f"{v_name}.json")
    if os.path.exists(v_path) and not os.path.exists(s_path):
        if not groq_api_key:
            st.warning("⚠️ Vui lòng cấu hình Groq API Key để nhận diện phụ đề.")
        else:
            with st.spinner("⚡ Groq Whisper đang nhận diện phụ đề (2-3 giây)..."):
                try:
                    segments_data = transcribe_with_groq(v_path, groq_api_key)
                    with open(s_path, "w", encoding="utf-8") as f:
                        json.dump(segments_data, f, ensure_ascii=False, indent=2)
                    st.success("Tạo phụ đề hoàn tất!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Lỗi Whisper API: {e}")

# ================= 7. HIỂN THỊ TRÌNH PHÁT VIDEO VÀ BÀI ÔN =================
all_ready_videos = {}
for f in os.listdir(MEDIA_DIR):
    if f.endswith(".json") and not f.endswith("_vocab.json"):
        v_base = f[:-5]
        v_file = os.path.join(MEDIA_DIR, f"{v_base}.mp4")
        if os.path.exists(v_file):
            try:
                with open(os.path.join(MEDIA_DIR, f), "r", encoding="utf-8") as jf:
                    subs_content = json.load(jf)
                    if isinstance(subs_content, list) and len(subs_content) > 0:
                        all_ready_videos[v_base] = {
                            # Đường dẫn tĩnh tương đối hỗ trợ HTTPS hoàn hảo trên Streamlit Cloud
                            "url": f"app/static/{urllib.parse.quote(v_base + '.mp4')}",
                            "subs": subs_content
                        }
            except Exception:
                pass

if all_ready_videos:
    video_list = list(all_ready_videos.keys())
    if "active_video_name" not in st.session_state or st.session_state["active_video_name"] not in video_list:
        st.session_state["active_video_name"] = video_list[0]

    col_v1, col_v2 = st.columns([5, 1.5])
    with col_v1:
        selected_vid = st.selectbox(
            "Chọn video học tập:",
            video_list,
            index=video_list.index(st.session_state["active_video_name"])
        )
        if selected_vid != st.session_state["active_video_name"]:
            st.session_state["active_video_name"] = selected_vid
            st.rerun()
    with col_v2:
        st.write("")
        st.write("")
        if st.button("🗑️ Xóa video này", use_container_width=True):
            remove_video_completely(selected_vid)
            st.session_state.pop("active_video_name", None)
            st.rerun()

    default_vid = st.session_state["active_video_name"]
    all_data_json = json.dumps(all_ready_videos, ensure_ascii=False)
    offline_dict_json = json.dumps(OFFLINE_DICTIONARY, ensure_ascii=False)

    tab_watch, tab_vocab = st.tabs(["🎬 Xem video & Phụ đề tương tác", "📝 Ôn tập từ vựng"])

    with tab_watch:
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background-color: #0f172a;
                margin: 0;
                padding: 6px;
                color: #f8fafc;
            }}
            .main-layout {{
                display: flex;
                flex-direction: column;
                gap: 10px;
                max-width: 1000px;
                margin: 0 auto;
            }}
            .video-container {{
                position: relative;
                width: 100%;
                background-color: #000000;
                border-radius: 10px;
                overflow: hidden;
            }}
            video {{
                width: 100%;
                max-height: 70vh;
                display: block;
                background: #000;
            }}
            .sub-overlay-box {{
                position: absolute;
                left: 50%;
                bottom: 45px;
                transform: translateX(-50%);
                width: 90%;
                text-align: center;
                background: rgba(15, 23, 42, 0.90);
                border: 1px solid rgba(255, 255, 255, 0.25);
                backdrop-filter: blur(6px);
                color: #ffffff;
                padding: 8px 12px;
                border-radius: 8px;
                font-size: 19px;
                line-height: 1.5;
                z-index: 50;
            }}
            .word-item {{
                display: inline-block;
                margin: 0 2px;
                padding: 1px 4px;
                border-radius: 4px;
                cursor: pointer;
                color: #ffffff;
            }}
            .word-item:hover, .word-item:active {{
                background-color: #facc15;
                color: #0f172a;
                font-weight: bold;
            }}
            #dict-popup {{
                position: absolute;
                display: none;
                z-index: 100;
                background: #1e293b;
                color: #ffffff;
                padding: 12px 16px;
                border-radius: 10px;
                border: 2px solid #38bdf8;
                box-shadow: 0 10px 30px rgba(0,0,0,0.8);
                font-size: 15px;
                max-width: 320px;
                min-width: 220px;
            }}
            #dict-popup .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 1px solid #334155;
                padding-bottom: 4px;
                margin-bottom: 6px;
            }}
            #dict-popup .word-title {{
                font-weight: bold;
                font-size: 18px;
                color: #facc15;
            }}
            #dict-popup .btn-speak {{
                background: #0284c7;
                border: none;
                color: white;
                border-radius: 4px;
                padding: 4px 8px;
                cursor: pointer;
                font-size: 12px;
            }}
            .transcript-card {{
                background: #ffffff;
                border-radius: 8px;
                padding: 12px;
                color: #1e293b;
            }}
            .transcript-scroll {{
                height: 160px;
                overflow-y: auto;
            }}
            .sub-line {{
                padding: 4px 8px;
                margin-bottom: 3px;
                border-radius: 4px;
                font-size: 15px;
            }}
            .sub-line.active {{
                background-color: #e0f2fe;
                font-weight: 600;
            }}
            .time-badge {{
                color: #0284c7;
                background: #f1f5f9;
                font-size: 11px;
                padding: 2px 4px;
                border-radius: 3px;
                cursor: pointer;
                margin-right: 6px;
            }}
        </style>
        </head>
        <body>
        <div class="main-layout">
            <div class="video-container" id="video-container">
                <video id="main-video" controls playsinline preload="auto"></video>
                <div class="sub-overlay-box" id="sub-overlay">
                    <span class="sub-text-content">▶ Bấm phát video để học</span>
                </div>
                <div id="dict-popup">
                    <div class="header">
                        <span class="word-title" id="pop-word">Word</span>
                        <button class="btn-speak" onclick="speakCurrentWord()">🔊 Đọc</button>
                    </div>
                    <div id="pop-meaning">Đang tra nghĩa...</div>
                </div>
            </div>
            <div class="transcript-card">
                <div style="font-weight:600; margin-bottom: 6px;">📜 Lời thoại đầy đủ:</div>
                <div class="transcript-scroll" id="transcript-scroll-box"></div>
            </div>
        </div>

        <script>
        const allVideos = {all_data_json};
        const OFFLINE_DICT = {offline_dict_json};
        let currentVideoName = "{default_vid}";
        let video, overlay, scrollBox, lines = [];
        let currentWordToSpeak = "";

        function initApp() {{
            video = document.getElementById('main-video');
            overlay = document.getElementById('sub-overlay');
            scrollBox = document.getElementById('transcript-scroll-box');

            video.addEventListener('timeupdate', () => {{
                const cur = video.currentTime;
                let activeLineFound = false;
                lines.forEach((line) => {{
                    const start = parseFloat(line.getAttribute('data-start'));
                    const end = parseFloat(line.getAttribute('data-end'));
                    if (cur >= start && cur <= end) {{
                        if (!line.classList.contains('active')) {{
                            lines.forEach(l => l.classList.remove('active'));
                            line.classList.add('active');
                            scrollBox.scrollTo({{
                                top: line.offsetTop - scrollBox.offsetTop - 40,
                                behavior: 'smooth'
                            }});
                            overlay.querySelector('.sub-text-content').innerHTML = line.querySelector('.line-text').innerHTML;
                        }}
                        activeLineFound = true;
                    }}
                }});
                if (!activeLineFound && cur > 0) {{
                    overlay.querySelector('.sub-text-content').innerHTML = "<span style='opacity:0.4;'>...</span>";
                }}
            }});

            document.addEventListener('click', (e) => {{
                const popup = document.getElementById('dict-popup');
                if (popup && !popup.contains(e.target) && !e.target.classList.contains('word-item')) {{
                    popup.style.display = 'none';
                }}
            }});

            setupVideo(currentVideoName);
        }}

        function setupVideo(name) {{
            const vidData = allVideos[name];
            if (!vidData) return;

            let subHtml = "";
            if (vidData.subs) {{
                vidData.subs.forEach((seg, idx) => {{
                    const m = String(Math.floor(seg.start / 60)).padStart(2, '0');
                    const s = String(Math.floor(seg.start % 60)).padStart(2, '0');
                    const timeBadge = `<span class='time-badge' onclick="video.currentTime=${{seg.start}};video.play();">[${{m}}:${{s}}]</span>`;

                    let words = "";
                    seg.text.trim().split(/\s+/).forEach(w => {{
                        const clean = w.replace(/[^a-zA-Z0-9]/g, "");
                        if (clean) {{
                            words += `<span class='word-item' onclick="lookupWord(event, '${{clean}}')">${{w}}</span> `;
                        }} else {{
                            words += w + " ";
                        }}
                    }});
                    subHtml += `<div class='sub-line' data-start='${{seg.start}}' data-end='${{seg.end}}'>${{timeBadge}}<span class='line-text'>${{words}}</span></div>`;
                }});
            }}

            scrollBox.innerHTML = subHtml;
            lines = document.querySelectorAll('.sub-line');
            video.src = vidData.url;
            video.load();
        }}

        function speakCurrentWord() {{
            if ('speechSynthesis' in window && currentWordToSpeak) {{
                const utterance = new SpeechSynthesisUtterance(currentWordToSpeak);
                utterance.lang = 'en-US';
                window.speechSynthesis.speak(utterance);
            }}
        }}

        async function lookupWord(event, word) {{
            event.stopPropagation();
            currentWordToSpeak = word;
            const popup = document.getElementById('dict-popup');
            const popWord = document.getElementById('pop-word');
            const popMeaning = document.getElementById('pop-meaning');

            popup.style.display = 'block';
            popup.style.left = '20px';
            popup.style.top = '30px';
            popWord.innerText = word;
            popMeaning.innerText = "⏳ Đang tra nghĩa...";
            speakCurrentWord();

            const wLower = word.toLowerCase();
            if (OFFLINE_DICT[wLower]) {{
                popMeaning.innerText = OFFLINE_DICT[wLower];
                return;
            }}

            try {{
                const res = await fetch(`https://api.mymemory.translated.net/get?q=${{encodeURIComponent(word)}}&langpair=en|vi`);
                const data = await res.json();
                if (data && data.responseData && data.responseData.translatedText) {{
                    popMeaning.innerText = data.responseData.translatedText;
                    return;
                }}
            }} catch(e) {{}}

            popMeaning.innerText = "Không tìm thấy nghĩa.";
        }}

        initApp();
        </script>
        </body>
        </html>
        """
        components.html(html_content, height=650, scrolling=False)

    with tab_vocab:
        st.subheader(f"🎯 Ôn tập từ vựng: `{default_vid}`")
        current_words = load_vocab_for_video(default_vid)

        with st.expander("➕ Thêm từ vựng mới vào bài học này"):
            c_add1, c_add2 = st.columns(2)
            new_w = c_add1.text_input("Từ tiếng Anh:")
            new_m = c_add2.text_input("Nghĩa tiếng Việt:")
            if st.button("Lưu từ"):
                if new_w and new_m:
                    add_vocab_entry(default_vid, new_w, new_m)
                    st.success("Đã thêm từ thành công!")
                    st.rerun()

        with st.expander("📋 Danh sách từ vựng hiện có", expanded=False):
            for idx, item in enumerate(list(current_words)):
                c1, c2, c3 = st.columns([2, 4, 1])
                c1.markdown(f"**{item['en']}**")
                c2.write(f": {item['vi']}")
                if c3.button("🗑️", key=f"del_{item['en']}_{idx}"):
                    remove_single_word(default_vid, item["en"])
                    st.rerun()

        st.divider()
        k_en_idx = f"rand_en_{default_vid}"
        k_vi_idx = f"rand_vi_{default_vid}"

        if k_en_idx not in st.session_state or st.session_state[k_en_idx] >= len(current_words):
            st.session_state[k_en_idx] = random.randint(0, len(current_words) - 1)
        if k_vi_idx not in st.session_state or st.session_state[k_vi_idx] >= len(current_words):
            st.session_state[k_vi_idx] = random.randint(0, len(current_words) - 1)

        w_en = current_words[st.session_state[k_en_idx]]
        w_vi = current_words[st.session_state[k_vi_idx]]

        col_q1, col_q2 = st.columns(2)
        with col_q1:
            st.markdown("#### 🇬🇧 ➔ 🇻🇳 Dịch sang Tiếng Việt")
            st.info(f"Từ: **{w_en['en']}**")
            with st.form(f"f_en_{w_en['en']}"):
                ans_vi = st.text_input("Nghĩa tiếng Việt:")
                if st.form_submit_button("Kiểm tra", use_container_width=True):
                    if check_en_to_vi_smart(ans_vi, w_en['en'], w_en['vi']):
                        st.success(f"🎉 Chính xác! Đáp án chuẩn: {w_en['vi']}")
                    else:
                        st.error(f"❌ Chưa đúng! Đáp án: {w_en['vi']}")

        with col_q2:
            st.markdown("#### 🇻🇳 ➔ 🇬🇧 Dịch sang Tiếng Anh")
            st.warning(f"Nghĩa: **{w_vi['vi']}** (Gợi ý: `{w_vi['hint']}`)")
            with st.form(f"f_vi_{w_vi['en']}"):
                ans_en = st.text_input("Từ tiếng Anh:")
                if st.form_submit_button("Kiểm tra", use_container_width=True):
                    if check_vi_to_en_smart(ans_en, w_vi['vi'], w_vi['en'], current_words):
                        st.success(f"🎉 Rất tốt! Đáp án: {w_vi['en']}")
                    else:
                        st.error(f"❌ Chưa đúng! Đáp án: {w_vi['en']}")
