import functools
import http.server
import json
import os
import random
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import yt_dlp
import streamlit as st
import streamlit.components.v1 as components

# Tận dụng CUDA có sẵn từ PyTorch nếu có
torch_lib_global = r"C:\Users\ASUS\AppData\Local\Programs\Python\Python310\Lib\site-packages\torch\lib"
if os.path.exists(torch_lib_global):
    try:
        os.add_dll_directory(torch_lib_global)
    except Exception:
        pass
    os.environ["PATH"] = torch_lib_global + os.pathsep + os.environ.get("PATH", "")

from faster_whisper import WhisperModel

st.set_page_config(page_title="Học Tiếng Anh Qua Video", layout="wide")
st.title("🎧 Học Tiếng Anh Tương Tác Cùng Video")

MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media_temp")
DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "offline_dict.json")
os.makedirs(MEDIA_DIR, exist_ok=True)
PORT = 8599

# DỮ LIỆU TỪ MẪU DỰ PHÒNG NẾU VIDEO MỚI CHƯA TRA TỪ NÀO
DEFAULT_SAMPLE_WORDS = [
    {"en": "conversation", "vi": "cuộc trò chuyện, đàm thoại, đối thoại", "hint": "c...n"},
    {"en": "express", "vi": "bày tỏ, biểu lộ, diễn đạt", "hint": "e...s"},
    {"en": "favorite", "vi": "yêu thích, ưa chuộng", "hint": "f...e"},
    {"en": "interview", "vi": "phỏng vấn, cuộc gặp mặt", "hint": "i...w"},
    {"en": "confidence", "vi": "sự tự tin, lòng tin, sự tin tưởng", "hint": "c...e"}
]

# ================= QUẢN LÝ TỪ ĐIỂN OFFLINE & TỪ VỰNG TÁCH BIỆT THEO TỪNG VIDEO =================
OFFLINE_DICTIONARY = {}
dict_lock = threading.Lock()

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
                with dict_lock:
                    OFFLINE_DICTIONARY = json.load(f)
            return
        except Exception as e:
            st.warning(f"Không thể nạp file từ điển offline: {e}")

    initial_dict = {
        "hello": "xin chào, chào bạn",
        "world": "thế giới, hoàn cầu",
        "video": "đoạn phim, video",
        "english": "tiếng anh",
        "learn": "học, học tập, nghiên cứu",
        "practice": "thực hành, rèn luyện, tập luyện",
        "done": "hoàn thành, xong, hoàn tất"
    }
    with dict_lock:
        OFFLINE_DICTIONARY = initial_dict
    try:
        with open(DICT_PATH, "w", encoding="utf-8") as f:
            json.dump(initial_dict, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def clean_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r'\s+', "_", name.strip())
    return name[:80] if name else "video_hoc_tap"

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
        with dict_lock:
            with open(v_vocab_file, "w", encoding="utf-8") as f:
                json.dump(vocab_list, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def add_vocab_entry(video_name: str, word: str, meaning: str):
    word = word.strip()
    meaning = meaning.strip()
    if not word or not meaning or "không tìm thấy" in meaning.lower() or "chưa có trong" in meaning.lower():
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
    mp4_file = os.path.join(MEDIA_DIR, f"{safe_name}.mp4")
    json_file = os.path.join(MEDIA_DIR, f"{safe_name}.json")
    vocab_file = get_video_vocab_path(safe_name)
    for fp in [mp4_file, json_file, vocab_file]:
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except Exception:
                pass

# ================= ĐỐI CHIẾU TỪ ĐỒNG NGHĨA THÔNG MINH HAI CHIỀU =================
def check_en_to_vi_smart(user_vi: str, target_en: str, target_vi_raw: str) -> bool:
    """Kiểm tra dịch Anh -> Việt: Chấp nhận mọi từ đồng nghĩa tiếng Việt hợp lệ."""
    u_clean = clean_text(user_vi)
    if not u_clean:
        return False
        
    acceptable = extract_meanings(target_vi_raw)
    if u_clean in acceptable or any(u_clean in m or m in u_clean for m in acceptable if len(u_clean) >= 2):
        return True
        
    with dict_lock:
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
    """Kiểm tra dịch Việt -> Anh: Chấp nhận mọi từ tiếng Anh đồng nghĩa có cùng nghĩa tiếng Việt."""
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
                
    with dict_lock:
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

# ================= HTTP SERVER RANGE VÀ API TRA TỪ =================
class RangeAndDictHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.path == "/save_word":
            length = int(self.headers.get("content-length", 0))
            body = self.rfile.read(length).decode("utf-8")
            try:
                data = json.loads(body)
                w = data.get("word", "").strip()
                m = data.get("meaning", "").strip()
                v_name = data.get("video_name", "").strip()
                if w and m:
                    with dict_lock:
                        OFFLINE_DICTIONARY[w.lower()] = m
                        with open(DICT_PATH, "w", encoding="utf-8") as f:
                            json.dump(OFFLINE_DICTIONARY, f, ensure_ascii=False, indent=2)
                    if v_name:
                        add_vocab_entry(v_name, w, m)
            except Exception:
                pass

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"status": "saved"}')
            return

        return super().do_POST()

    def do_GET(self):
        if self.path.startswith("/lookup?"):
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            word = params.get("word", [""])[0].strip().lower()

            with dict_lock:
                meaning = OFFLINE_DICTIONARY.get(word, None)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()

            response = {
                "found": meaning is not None,
                "word": word,
                "meaning": meaning if meaning else ""
            }
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
            return

        return super().do_GET()

    def send_head(self):
        path = self.translate_path(self.path)
        if not os.path.exists(path) or os.path.isdir(path):
            return super().send_head()
        
        ctype = "video/mp4" if path.endswith(".mp4") else self.guess_type(path)
        try:
            f = open(path, 'rb')
        except OSError:
            self.send_error(404, "File not found")
            return None

        fs = os.fstat(f.fileno())
        size = fs[6]
        range_header = self.headers.get('Range')

        if range_header:
            match = re.match(r'^bytes=(\d+)-(\d*)$', range_header.strip())
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
                if start >= size:
                    self.send_error(416, "Requested Range Not Satisfiable")
                    f.close()
                    return None
                end = min(end, size - 1)
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                f.seek(start)
                return f

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return f

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True
    def server_bind(self):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super().server_bind()

@st.cache_resource
def start_video_server():
    handler = functools.partial(RangeAndDictHTTPRequestHandler, directory=MEDIA_DIR)
    try:
        httpd = ReusableTCPServer(("127.0.0.1", PORT), handler)
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
    except OSError:
        pass
    return True

start_video_server()

@st.cache_resource
def load_model():
    try:
        return WhisperModel("base", device="cuda", compute_type="float16")
    except Exception:
        return WhisperModel("base", device="cpu", compute_type="int8")

def open_file_dialog() -> str:
    ps_cmd = (
        "[System.Reflection.Assembly]::LoadWithPartialName('System.windows.forms') | Out-Null; "
        "$f = New-Object System.Windows.Forms.OpenFileDialog; "
        "$f.Filter = 'Video Files (*.mp4;*.mkv;*.avi;*.mov)|*.mp4;*.mkv;*.avi;*.mov|All Files (*.*)|*.*'; "
        "$f.Title = 'Chọn video để học'; "
        "$res = $f.ShowDialog(); "
        "if ($res -eq 'OK') { Write-Output $f.FileName }"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        return proc.stdout.strip()
    except Exception:
        return ""

# ================= SIDEBAR =================
with st.sidebar:
    st.header("⚙️ Chế độ Tra Từ Điển")
    dict_mode_selection = st.radio(
        "Chọn nguồn từ điển mong muốn:",
        [
            "⚡ Tự động (Ưu tiên Online -> Offline)",
            "💾 Thuần Offline (Chỉ tra trong máy)",
            "🌐 Thuần Online (Chỉ dịch qua API)"
        ],
        index=0
    )

    if "Thuần Offline" in dict_mode_selection:
        active_mode = "offline_only"
    elif "Thuần Online" in dict_mode_selection:
        active_mode = "online_only"
    else:
        active_mode = "hybrid_online_first"

    st.divider()
    st.header("📊 Trạng thái từ điển:")
    st.metric(label="Tổng số từ trong máy", value=f"{len(OFFLINE_DICTIONARY):,} từ")

    st.divider()
    st.header("⌨️ Phím tắt & Thao tác:")
    st.markdown("""
    * **Click vào video / Phím Space**: Dừng / Tiếp tục phát
    * **Nhấp đúp vào video**: Bật / Tắt Toàn màn hình
    * **Click vào chữ**: Tra từ & hiện ô nghĩa đè lên trước sub
    * **Giữ chuột kéo phụ đề**: Căn chỉnh độ cao phụ đề
    * **⬅ Mũi tên trái**: Lùi 5 giây
    * **➡ Mũi tên phải**: Tiến 5 giây
    """)
    st.divider()
    if st.button("🧹 Xóa toàn bộ kho video"):
        for f in os.listdir(MEDIA_DIR):
            fp = os.path.join(MEDIA_DIR, f)
            try:
                if os.path.isfile(fp) or os.path.islink(fp): os.unlink(fp)
                elif os.path.isdir(fp): shutil.rmtree(fp)
            except Exception: pass
        if "active_video_name" in st.session_state:
            del st.session_state["active_video_name"]
        st.success("Đã dọn sạch toàn bộ video và đặt lại danh sách từ vựng từ đầu!")
        st.rerun()

source_option = st.radio(
    "Thêm video mới vào kho học tập:",
    ["📂 Chọn file trực tiếp từ máy tính (Offline)", "🔗 Dán link YouTube (Online)"],
    horizontal=True
)

if source_option == "📂 Chọn file trực tiếp từ máy tính (Offline)":
    col1, col2 = st.columns([3, 7])
    with col1:
        if st.button("📁 Bấm để duyệt file..."):
            chosen_file = open_file_dialog()
            if chosen_file and os.path.exists(chosen_file):
                base_name = clean_filename(os.path.splitext(os.path.basename(chosen_file))[0])
                target_path = os.path.join(MEDIA_DIR, f"{base_name}.mp4")

                if not os.path.exists(target_path):
                    try:
                        os.link(chosen_file, target_path)
                    except Exception:
                        with open(chosen_file, 'rb') as src, open(target_path, 'wb') as dst:
                            shutil.copyfileobj(src, dst, length=16*1024*1024)
                
                st.session_state["active_video_name"] = base_name
                st.rerun()
            elif chosen_file:
                st.error("Không thể mở file đã chọn!")
    with col2:
        if "active_video_name" in st.session_state:
            st.info(f"Video đang chọn: **{st.session_state['active_video_name']}**")

elif source_option == "🔗 Dán link YouTube (Online)":
    yt_url = st.text_input("Dán đường link YouTube vào đây:", placeholder="https://www.youtube.com/watch?v=...")
    if yt_url and st.button("🚀 Tải video YouTube vào kho"):
        with st.spinner("Đang tải video từ YouTube..."):
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

if "active_video_name" in st.session_state:
    v_name = st.session_state["active_video_name"]
    v_path = os.path.join(MEDIA_DIR, f"{v_name}.mp4")
    s_path = os.path.join(MEDIA_DIR, f"{v_name}.json")
    if os.path.exists(v_path) and not os.path.exists(s_path):
        with st.spinner(f"Đang nhận diện giọng nói cho `{v_name}` bằng Whisper..."):
            model = load_model()
            segments, _ = model.transcribe(v_path, language="en", beam_size=1)
            segments_data = []
            for seg in segments:
                segments_data.append({
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text
                })
            with open(s_path, "w", encoding="utf-8") as f:
                json.dump(segments_data, f, ensure_ascii=False, indent=2)

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
                            "url": f"http://127.0.0.1:{PORT}/{urllib.parse.quote(v_base + '.mp4')}?t={int(os.path.getmtime(v_file))}",
                            "subs": subs_content
                        }
            except Exception:
                pass

if all_ready_videos:
    video_list = list(all_ready_videos.keys())
    if "active_video_name" not in st.session_state or st.session_state["active_video_name"] not in video_list:
        st.session_state["active_video_name"] = video_list[0]

    col_pick, col_del = st.columns([5, 1.5])
    with col_pick:
        selected_vid = st.selectbox(
            "⚡ Chọn video học tập:",
            video_list,
            index=video_list.index(st.session_state["active_video_name"])
        )
        if selected_vid != st.session_state["active_video_name"]:
            st.session_state["active_video_name"] = selected_vid
            st.rerun()

    with col_del:
        st.write("")
        st.write("")
        if st.button("🗑️ Xóa video này", key="btn_del_single_video", use_container_width=True):
            st.session_state["confirm_del_single"] = True

    if st.session_state.get("confirm_del_single", False):
        st.warning(f"⚠️ Bạn có chắc muốn xóa video **{selected_vid}** cùng toàn bộ file từ vựng liên quan?")
        c_yes, c_no = st.columns([1, 1])
        with c_yes:
            if st.button("Xác nhận xóa vĩnh viễn", type="primary", use_container_width=True):
                remove_video_completely(selected_vid)
                st.session_state["confirm_del_single"] = False
                del st.session_state["active_video_name"]
                st.success("Đã xóa video và dữ liệu từ vựng thành công!")
                st.rerun()
        with c_no:
            if st.button("Hủy", use_container_width=True):
                st.session_state["confirm_del_single"] = False
                st.rerun()

    default_vid = st.session_state["active_video_name"]
    all_data_json = json.dumps(all_ready_videos, ensure_ascii=False)

    tab_watch, tab_vocab = st.tabs(["🎬 Xem video & Phụ đề tương tác", "📝 Ôn tập từ vựng của video này"])

    with tab_watch:
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8">
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background-color: #0f172a;
                margin: 0;
                padding: 10px;
                color: #f8fafc;
                outline: none;
            }}
            .main-layout {{
                display: flex;
                flex-direction: column;
                gap: 12px;
                max-width: 1100px;
                margin: 0 auto;
            }}
            .video-container {{
                position: relative;
                width: 100%;
                background-color: #000000;
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 10px 25px rgba(0,0,0,0.5);
                outline: none;
                user-select: none;
            }}
            .video-container:fullscreen,
            .video-container:-webkit-full-screen {{
                width: 100vw !important;
                height: 100vh !important;
                position: fixed !important;
                top: 0 !important;
                left: 0 !important;
                margin: 0 !important;
                border-radius: 0 !important;
                background-color: #000000 !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                z-index: 999999 !important;
            }}
            video {{
                width: 100%;
                height: 560px;
                display: block;
                object-fit: contain;
                background: #000000;
            }}
            .video-container:fullscreen video,
            .video-container:-webkit-full-screen video {{
                width: 100vw !important;
                height: 100vh !important;
                max-height: 100vh !important;
            }}
            .video-click-surface {{
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: calc(100% - 60px);
                z-index: 20;
                cursor: pointer;
            }}
            .video-container:fullscreen .video-click-surface,
            .video-container:-webkit-full-screen .video-click-surface {{
                height: calc(100% - 80px) !important;
            }}
            video::-webkit-media-controls-fullscreen-button {{
                display: none !important;
            }}
            .fs-btn {{
                position: absolute;
                top: 15px;
                right: 15px;
                background: rgba(15, 23, 42, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.3);
                color: #ffffff;
                padding: 8px 14px;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
                z-index: 2147483645;
                backdrop-filter: blur(4px);
                transition: all 0.2s;
            }}
            .fs-btn:hover {{
                background: #0284c7;
                border-color: #38bdf8;
            }}
            .seek-indicator {{
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                background: rgba(15, 23, 42, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.2);
                color: #facc15;
                font-size: 26px;
                font-weight: bold;
                padding: 12px 28px;
                border-radius: 30px;
                display: none;
                pointer-events: none;
                z-index: 2147483645;
                box-shadow: 0 4px 20px rgba(0,0,0,0.5);
                backdrop-filter: blur(6px);
                transition: opacity 0.2s;
            }}
            .sub-overlay-box {{
                position: absolute;
                left: 50% !important;
                bottom: 58px;
                transform: translateX(-50%) !important;
                max-width: 88%;
                min-width: 35%;
                text-align: center;
                background: rgba(15, 23, 42, 0.92);
                border: 1px solid rgba(255, 255, 255, 0.25);
                backdrop-filter: blur(8px);
                color: #ffffff !important;
                padding: 8px 22px 12px 22px;
                border-radius: 12px;
                font-size: 22px;
                font-weight: 500;
                line-height: 1.6;
                box-shadow: 0 8px 30px rgba(0,0,0,0.8);
                z-index: 2147483640;
                cursor: ns-resize;
                user-select: none;
                touch-action: none;
            }}
            .drag-handle-bar {{
                width: 46px;
                height: 4px;
                background: rgba(255, 255, 255, 0.35);
                border-radius: 3px;
                margin: 0 auto 6px auto;
                pointer-events: none;
            }}
            .video-container:fullscreen .sub-overlay-box,
            .video-container:-webkit-full-screen .sub-overlay-box {{
                font-size: 30px !important;
                bottom: 80px !important;
                top: auto !important;
                padding: 14px 30px 18px 30px !important;
                z-index: 2147483640 !important;
                display: block !important;
                visibility: visible !important;
            }}
            .word-item {{
                display: inline-block;
                margin: 0 3px;
                padding: 2px 6px;
                border-radius: 5px;
                cursor: pointer;
                color: #ffffff !important;
                transition: all 0.15s;
            }}
            .word-item:hover {{
                background-color: #facc15 !important;
                color: #0f172a !important;
                font-weight: bold;
                transform: translateY(-2px);
            }}
            #dict-popup {{
                position: absolute;
                display: none;
                z-index: 2147483647 !important;
                background: #1e293b !important;
                color: #ffffff;
                padding: 14px 18px;
                border-radius: 10px;
                border: 2px solid #38bdf8 !important;
                box-shadow: 0 14px 40px rgba(0,0,0,0.95);
                font-size: 15px;
                max-width: 380px;
                min-width: 260px;
                max-height: 280px;
                cursor: default;
                pointer-events: auto;
            }}
            #dict-popup .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 1px solid #334155;
                padding-bottom: 6px;
                margin-bottom: 8px;
            }}
            #dict-popup .word-title {{
                font-weight: bold;
                font-size: 20px;
                color: #facc15;
            }}
            #dict-popup .btn-speak {{
                background: #0284c7;
                border: none;
                color: white;
                border-radius: 4px;
                padding: 4px 10px;
                cursor: pointer;
                font-size: 12px;
                font-weight: bold;
            }}
            #dict-popup .btn-speak:hover {{
                background: #0369a1;
            }}
            #dict-popup .meaning {{
                line-height: 1.6;
                color: #cbd5e1;
                max-height: 180px;
                overflow-y: auto;
                white-space: pre-line;
            }}
            .dict-source-badge {{
                display: inline-block;
                font-size: 11px;
                padding: 2px 7px;
                border-radius: 4px;
                margin-left: 8px;
                font-weight: bold;
            }}
            .badge-offline {{ background: #16a34a; color: #ffffff; }}
            .badge-online {{ background: #2563eb; color: #ffffff; }}
            .badge-warn {{ background: #d97706; color: #ffffff; }}

            .transcript-card {{
                background: #ffffff;
                border-radius: 10px;
                padding: 15px;
                color: #1e293b !important;
                box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            }}
            .transcript-title {{
                font-size: 15px;
                font-weight: 600;
                margin-bottom: 10px;
                color: #475569;
            }}
            .transcript-scroll {{
                height: 180px;
                overflow-y: auto;
                padding-right: 8px;
            }}
            .sub-line {{
                padding: 6px 10px;
                margin-bottom: 4px;
                border-radius: 6px;
                font-size: 16px;
                color: #1e293b !important;
                border-left: 3px solid transparent;
                transition: background-color 0.2s;
            }}
            .sub-line.active {{
                background-color: #e0f2fe !important;
                border-left: 3px solid #0284c7 !important;
                font-weight: 600;
            }}
            .time-badge {{
                color: #0284c7;
                background: #f1f5f9;
                font-size: 12px;
                font-family: monospace;
                padding: 2px 6px;
                border-radius: 4px;
                cursor: pointer;
                margin-right: 8px;
            }}
            .time-badge:hover {{
                background: #0284c7;
                color: #ffffff;
            }}
            .transcript-scroll .word-item {{
                color: #1e293b !important;
            }}
            .transcript-scroll .word-item:hover {{
                background: #fef08a !important;
                color: #000 !important;
            }}
        </style>
        </head>
        <body tabindex="0">

        <div class="main-layout">
            <div class="video-container" id="video-container">
                <button class="fs-btn" id="fs-toggle-btn" onclick="toggleFullScreen(event)">⛶ Toàn màn hình</button>
                <div class="seek-indicator" id="seek-indicator">+5s</div>

                <video id="main-video" controls controlslist="nofullscreen" playsinline preload="auto"></video>
                <div class="video-click-surface" id="video-click-surface"></div>

                <div class="sub-overlay-box" id="sub-overlay">
                    <div class="drag-handle-bar"></div>
                    <span class="sub-text-content">▶ Bấm phát video để học</span>
                </div>

                <div id="dict-popup">
                    <div class="header">
                        <div>
                            <span class="word-title" id="pop-word">Word</span>
                            <span id="pop-badge" class="dict-source-badge badge-online">Online</span>
                        </div>
                        <button class="btn-speak" onclick="speakCurrentWord()">🔊 Nghe</button>
                    </div>
                    <div class="meaning" id="pop-meaning">Đang tra...</div>
                </div>
            </div>

            <div class="transcript-card">
                <div class="transcript-title">📜 Lời thoại đầy đủ (Bấm vào thời gian để tua):</div>
                <div class="transcript-scroll" id="transcript-scroll-box"></div>
            </div>
        </div>

        <script>
        const allVideos = {all_data_json};
        const CURRENT_DICT_MODE = "{active_mode}";
        let currentVideoName = "{default_vid}";
        
        let container, video, clickSurface, overlay, scrollBox, fsBtn, seekIndicator;
        let lines = [];
        let currentWordToSpeak = "";
        let seekTimeout = null;

        let isDraggingSub = false;
        let dragStartY = 0;
        let overlayStartTop = 0;
        let hasMoved = false;

        function initApp() {{
            container = document.getElementById('video-container');
            video = document.getElementById('main-video');
            clickSurface = document.getElementById('video-click-surface');
            overlay = document.getElementById('sub-overlay');
            scrollBox = document.getElementById('transcript-scroll-box');
            fsBtn = document.getElementById('fs-toggle-btn');
            seekIndicator = document.getElementById('seek-indicator');

            overlay.addEventListener('mousedown', (e) => {{
                if (e.target.closest('#dict-popup') || e.target.closest('.btn-speak')) return;
                isDraggingSub = true;
                hasMoved = false;
                dragStartY = e.clientY;
                const rect = overlay.getBoundingClientRect();
                const containerRect = container.getBoundingClientRect();
                overlayStartTop = rect.top - containerRect.top;
                overlay.style.bottom = 'auto';
                overlay.style.top = overlayStartTop + 'px';
            }});

            window.addEventListener('mousemove', (e) => {{
                if (!isDraggingSub) return;
                const deltaY = e.clientY - dragStartY;
                if (Math.abs(deltaY) > 4) hasMoved = true;
                const containerRect = container.getBoundingClientRect();
                const overlayHeight = overlay.offsetHeight;
                let newTop = overlayStartTop + deltaY;
                newTop = Math.max(10, Math.min(newTop, containerRect.height - overlayHeight - 65));
                overlay.style.top = newTop + 'px';
            }});

            window.addEventListener('mouseup', () => {{
                isDraggingSub = false;
            }});

            clickSurface.addEventListener('click', (e) => {{
                e.stopPropagation();
                togglePlayPause();
            }});

            clickSurface.addEventListener('dblclick', (e) => {{
                e.stopPropagation();
                toggleFullScreen(e);
            }});

            window.addEventListener('keydown', (e) => {{
                if (['input', 'textarea', 'select'].includes(document.activeElement.tagName.toLowerCase())) return;
                if (e.key === ' ' || e.code === 'Space') {{
                    e.preventDefault();
                    togglePlayPause();
                }} else if (e.key === 'ArrowLeft') {{
                    e.preventDefault();
                    video.currentTime = Math.max(0, video.currentTime - 5);
                    showSeekFeedback('⏪ -5s');
                }} else if (e.key === 'ArrowRight') {{
                    e.preventDefault();
                    video.currentTime = Math.min(video.duration, video.currentTime + 5);
                    showSeekFeedback('⏩ +5s');
                }}
            }});

            document.addEventListener('fullscreenchange', handleFullscreenChange);
            document.addEventListener('webkitfullscreenchange', handleFullscreenChange);

            video.addEventListener('timeupdate', () => {{
                const cur = video.currentTime;
                if (cur > 0) {{
                    try {{ localStorage.setItem("watched_time_" + currentVideoName, cur); }} catch(e) {{}}
                }}

                let activeLineFound = false;
                lines.forEach((line) => {{
                    const start = parseFloat(line.getAttribute('data-start'));
                    const end = parseFloat(line.getAttribute('data-end'));

                    if (cur >= start && cur <= end) {{
                        if (!line.classList.contains('active')) {{
                            lines.forEach(l => l.classList.remove('active'));
                            line.classList.add('active');
                            const lineTop = line.offsetTop - scrollBox.offsetTop;
                            scrollBox.scrollTo({{
                                top: lineTop - (scrollBox.clientHeight / 2) + 20,
                                behavior: 'smooth'
                            }});
                            overlay.querySelector('.sub-text-content').innerHTML = line.querySelector('.line-text').innerHTML;
                        }}
                        activeLineFound = true;
                    }}
                }});

                if (!activeLineFound && cur > 0) {{
                    overlay.querySelector('.sub-text-content').innerHTML = "<span style='opacity: 0.35;'>...</span>";
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

        function setupVideo(videoName) {{
            const keys = Object.keys(allVideos);
            if (keys.length === 0) return;
            if (!allVideos[videoName]) {{
                videoName = keys[0];
            }}
            currentVideoName = videoName;
            const vidData = allVideos[videoName];
            if (!vidData) return;

            let subHtml = "";
            if (vidData.subs && Array.isArray(vidData.subs)) {{
                vidData.subs.forEach((seg, idx) => {{
                    const m_start = String(Math.floor(seg.start / 60)).padStart(2, '0');
                    const s_start = String(Math.floor(seg.start % 60)).padStart(2, '0');
                    const timeBadge = `<span class='time-badge' onclick="seekVideo(${{seg.start}})">[${{m_start}}:${{s_start}}]</span>`;

                    let wordsHtml = "";
                    seg.text.trim().split(/\s+/).forEach(word => {{
                        const cleanWord = word.replace(/[^a-zA-Z0-9]/g, "");
                        if (cleanWord) {{
                            wordsHtml += `<span class='word-item' onclick="lookupWord(event, this, '${{cleanWord}}')">${{word}}</span> `;
                        }} else {{
                            wordsHtml += word + " ";
                        }}
                    }});

                    subHtml += `
                    <div class='sub-line' id='sub-${{idx}}' data-start='${{seg.start}}' data-end='${{seg.end}}'>
                        ${{timeBadge}} <span class='line-text'>${{wordsHtml}}</span>
                    </div>`;
                }});
            }}

            scrollBox.innerHTML = subHtml;
            lines = document.querySelectorAll('.sub-line');
            overlay.querySelector('.sub-text-content').innerHTML = "▶ Bấm phát video để học";

            video.pause();
            video.src = vidData.url;
            video.load();

            video.onloadedmetadata = () => {{
                try {{
                    const savedTime = localStorage.getItem("watched_time_" + currentVideoName);
                    if (savedTime && parseFloat(savedTime) > 0 && parseFloat(savedTime) < video.duration) {{
                        video.currentTime = parseFloat(savedTime);
                    }}
                }} catch(e) {{}}
            }};

            video.onerror = () => {{
                overlay.querySelector('.sub-text-content').innerHTML = "⚠️ Không thể tải video từ server 127.0.0.1:" + {PORT};
            }};
        }}

        function showSeekFeedback(text) {{
            if (!seekIndicator) return;
            seekIndicator.innerText = text;
            seekIndicator.style.display = 'block';
            seekIndicator.style.opacity = '1';
            if (seekTimeout) clearTimeout(seekTimeout);
            seekTimeout = setTimeout(() => {{
                seekIndicator.style.opacity = '0';
                setTimeout(() => {{ seekIndicator.style.display = 'none'; }}, 200);
            }}, 400);
        }}

        function togglePlayPause() {{
            if (!video) return;
            if (video.paused) {{
                video.play();
                showSeekFeedback('▶ Tiếp tục');
            }} else {{
                video.pause();
                showSeekFeedback('⏸ Tạm dừng');
            }}
        }}

        function toggleFullScreen(e) {{
            if (e) e.stopPropagation();
            if (!document.fullscreenElement && !document.webkitFullscreenElement) {{
                if (container.requestFullscreen) {{
                    container.requestFullscreen();
                }} else if (container.webkitRequestFullscreen) {{
                    container.webkitRequestFullscreen();
                }}
            }} else {{
                if (document.exitFullscreen) {{
                    document.exitFullscreen();
                }} else if (document.webkitExitFullscreen) {{
                    document.webkitExitFullscreen();
                }}
            }}
        }}

        function handleFullscreenChange() {{
            const isFull = !!(document.fullscreenElement || document.webkitFullscreenElement);
            if (fsBtn) {{
                fsBtn.innerText = isFull ? "✖ Thoát Full" : "⛶ Toàn màn hình";
            }}
            if (overlay) {{
                overlay.style.top = '';
                overlay.style.bottom = isFull ? '80px' : '58px';
            }}
        }}

        function seekVideo(seconds) {{
            if (!video) return;
            video.currentTime = seconds;
            video.play();
        }}

        function speakCurrentWord() {{
            if ('speechSynthesis' in window && currentWordToSpeak) {{
                const utterance = new SpeechSynthesisUtterance(currentWordToSpeak);
                utterance.lang = 'en-US';
                utterance.rate = 0.9;
                window.speechSynthesis.speak(utterance);
            }}
        }}

        async function fetchLocalOfflineWord(word) {{
            try {{
                const res = await fetch(`http://127.0.0.1:{PORT}/lookup?word=${{encodeURIComponent(word)}}`);
                const data = await res.json();
                if (data && data.found) return data.meaning;
            }} catch (e) {{}}
            return null;
        }}

        function saveWordToServer(w, m) {{
            fetch(`http://127.0.0.1:{PORT}/save_word`, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ 
                    word: w, 
                    meaning: m, 
                    video_name: currentVideoName 
                }})
            }}).catch(() => {{}});
        }}

        async function lookupWord(event, el, word) {{
            event.stopPropagation();
            if (hasMoved) return;

            currentWordToSpeak = word;
            const popup = document.getElementById('dict-popup');
            const popWord = document.getElementById('pop-word');
            const popMeaning = document.getElementById('pop-meaning');
            const popBadge = document.getElementById('pop-badge');

            const containerRect = container.getBoundingClientRect();
            const wordRect = el.getBoundingClientRect();

            popup.style.display = 'block';
            const popW = popup.offsetWidth || 300;
            const popH = popup.offsetHeight || 150;

            let leftPos = (wordRect.left - containerRect.left) + (wordRect.width / 2) - (popW / 2);
            if (leftPos < 12) leftPos = 12;
            if (leftPos + popW > containerRect.width - 12) leftPos = containerRect.width - popW - 12;

            let topPos = (wordRect.top - containerRect.top) - popH - 14;
            if (topPos < 10) {{
                topPos = (wordRect.bottom - containerRect.top) + 14;
            }}
            if (topPos + popH > containerRect.height - 60) {{
                topPos = Math.max(10, containerRect.height - popH - 65);
            }}

            popup.style.left = leftPos + 'px';
            popup.style.top = topPos + 'px';

            popWord.innerText = word;
            popMeaning.innerText = "⏳ Đang tra nghĩa...";
            speakCurrentWord();

            if (CURRENT_DICT_MODE === "offline_only") {{
                popBadge.innerText = "Offline";
                popBadge.className = "dict-source-badge badge-offline";
                const localMeaning = await fetchLocalOfflineWord(word);
                if (localMeaning) {{
                    popMeaning.innerText = localMeaning;
                    saveWordToServer(word, localMeaning);
                }} else {{
                    popMeaning.innerText = "Từ này chưa có trong từ điển offline.";
                }}
                return;
            }}

            if (CURRENT_DICT_MODE === "online_only") {{
                popBadge.innerText = "Online";
                popBadge.className = "dict-source-badge badge-online";
                try {{
                    const res = await fetch(`https://api.mymemory.translated.net/get?q=${{encodeURIComponent(word)}}&langpair=en|vi`);
                    const data = await res.json();
                    if (data && data.responseData && data.responseData.translatedText) {{
                        const resText = data.responseData.translatedText;
                        popMeaning.innerText = resText;
                        saveWordToServer(word, resText);
                    }} else {{
                        popMeaning.innerText = "Không tìm thấy kết quả từ API.";
                    }}
                }} catch (err) {{
                    popMeaning.innerText = "Không có kết nối mạng Internet.";
                }}
                return;
            }}

            popBadge.innerText = "Online";
            popBadge.className = "dict-source-badge badge-online";
            popMeaning.innerText = "⏳ Đang tra online...";

            try {{
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 3500);

                const res = await fetch(
                    `https://api.mymemory.translated.net/get?q=${{encodeURIComponent(word)}}&langpair=en|vi`,
                    {{ signal: controller.signal }}
                );
                clearTimeout(timeoutId);

                const data = await res.json();
                if (data && data.responseData && data.responseData.translatedText) {{
                    const onlineText = data.responseData.translatedText;
                    popMeaning.innerText = onlineText;
                    saveWordToServer(word, onlineText);
                    return;
                }}
            }} catch (err) {{}}

            const fallbackMeaning = await fetchLocalOfflineWord(word);
            if (fallbackMeaning) {{
                popBadge.innerText = "Offline (Dự phòng)";
                popBadge.className = "dict-source-badge badge-offline";
                popMeaning.innerText = fallbackMeaning;
                saveWordToServer(word, fallbackMeaning);
            }} else {{
                popBadge.innerText = "Không tìm thấy";
                popBadge.className = "dict-source-badge badge-warn";
                popMeaning.innerText = "Không thể kết nối Internet và từ này chưa có trong file offline.";
            }}
        }}

        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', initApp);
        }} else {{
            initApp();
        }}
        </script>
        </body>
        </html>
        """
        components.html(html_content, height=890, scrolling=False)

    # ================= TAB 2: ÔN TẬP TỪ VỰNG CỦA RIÊNG VIDEO ĐANG CHỌN =================
    with tab_vocab:
        st.subheader(f"🎯 Phòng Ôn Tập Từ Vựng: `{default_vid}`")
        
        current_words = load_vocab_for_video(default_vid)

        if not current_words:
            st.info("💡 Hiện chưa có từ vựng nào trong danh sách ôn tập của video này. Bạn hãy sang Tab 1, click vào các từ trên phụ đề để tự động thêm vào đây!")
        else:
            st.markdown(f"**Tổng số từ vựng của video này:** `{len(current_words)} từ` *(Bấm tra từ trên video để nạp thêm)*")

            with st.expander("📋 Xem & Xóa bớt từ trong danh sách của video này", expanded=False):
                st.caption("💡 Bạn có thể bấm nút 🗑️ bên cạnh bất kỳ từ nào để xóa từ đó khỏi danh sách học.")
                for idx, item in enumerate(list(current_words)):
                    col_idx, col_en, col_vi, col_del = st.columns([0.5, 2, 4, 1])
                    col_idx.write(f"{idx+1}.")
                    col_en.markdown(f"**{item['en']}**")
                    col_vi.write(f": {item['vi']}")
                    if col_del.button("🗑️", key=f"btn_del_list_{default_vid}_{item['en']}_{idx}", help="Xóa từ này"):
                        remove_single_word(default_vid, item["en"])
                        st.rerun()

            st.divider()

            k_en_idx = f"rand_en_{default_vid}"
            k_vi_idx = f"rand_vi_{default_vid}"

            if k_en_idx not in st.session_state or st.session_state[k_en_idx] >= len(current_words):
                st.session_state[k_en_idx] = random.randint(0, len(current_words) - 1)
            if k_vi_idx not in st.session_state or st.session_state[k_vi_idx] >= len(current_words):
                st.session_state[k_vi_idx] = random.randint(0, len(current_words) - 1)

            word_for_en = current_words[st.session_state[k_en_idx]]
            word_for_vi = current_words[st.session_state[k_vi_idx]]

            col_top_l, col_top_r = st.columns([3, 1])
            with col_top_l:
                st.markdown("### 🎲 Thử Thách Luyện Dịch Hai Chiều")
            with col_top_r:
                if st.button("🔄 Đổi cả 2 từ ngẫu nhiên", use_container_width=True):
                    st.session_state[k_en_idx] = random.randint(0, len(current_words) - 1)
                    st.session_state[k_vi_idx] = random.randint(0, len(current_words) - 1)
                    st.session_state.pop(f"show_ans_en_{default_vid}", None)
                    st.session_state.pop(f"show_ans_vi_{default_vid}", None)
                    st.rerun()

            col_q1, col_q2 = st.columns(2)

            # ---------------- PHẦN 1: ANH ➔ VIỆT ----------------
            with col_q1:
                st.markdown("#### 🇬🇧 ➔ 🇻🇳 Dịch sang Tiếng Việt")
                st.markdown(
                    f"""
                    <div style="background-color: #0284c7; padding: 16px; border-radius: 10px; text-align: center; margin-bottom: 12px;">
                        <span style="font-size: 14px; color: #e0f2fe; text-transform: uppercase; letter-spacing: 1px;">Từ tiếng Anh:</span><br>
                        <span style="font-size: 32px; font-weight: bold; color: #ffffff;">{word_for_en['en']}</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                with st.form(f"form_en_vi_{default_vid}_{st.session_state[k_en_idx]}"):
                    user_vi = st.text_input(
                        "Nhập nghĩa tiếng Việt (Gõ xong nhấn Enter):", 
                        key=f"input_vi_{default_vid}_{st.session_state[k_en_idx]}"
                    )
                    sub_vi = st.form_submit_button("Kiểm tra (Hoặc nhấn Enter)", use_container_width=True)
                    
                    if sub_vi:
                        if check_en_to_vi_smart(user_vi, word_for_en['en'], word_for_en['vi']):
                            st.session_state[f"correct_vi_{default_vid}_{word_for_en['en']}"] = True
                            st.success(f"🎉 Chính xác! Đáp án chuẩn: **{word_for_en['vi']}** (chấp nhận cả '{user_vi.strip()}')")
                        else:
                            st.session_state[f"correct_vi_{default_vid}_{word_for_en['en']}"] = False
                            st.error(f"❌ Chưa chính xác! Gợi ý nghĩa chuẩn: **{word_for_en['vi']}**")

                if st.session_state.get(f"correct_vi_{default_vid}_{word_for_en['en']}", False):
                    if st.button(f"🗑️ Đã thuộc từ '{word_for_en['en']}' - Xóa khỏi bài ôn", key=f"btn_del_done_en_{word_for_en['en']}", use_container_width=True):
                        remove_single_word(default_vid, word_for_en["en"])
                        st.session_state.pop(f"correct_vi_{default_vid}_{word_for_en['en']}", None)
                        st.session_state.pop(f"show_ans_vi_{default_vid}", None)
                        st.rerun()

                col_btn_l1, col_btn_l2 = st.columns(2)
                with col_btn_l1:
                    if st.button("👁️ Xem đáp án", key="btn_show_ans_vi", use_container_width=True):
                        st.session_state[f"show_ans_vi_{default_vid}"] = True

                with col_btn_l2:
                    if st.button("Từ tiếng Anh khác ➡️", key="btn_next_random_en", use_container_width=True):
                        st.session_state[k_en_idx] = random.randint(0, len(current_words) - 1)
                        st.session_state.pop(f"show_ans_vi_{default_vid}", None)
                        st.session_state.pop(f"correct_vi_{default_vid}_{word_for_en['en']}", None)
                        st.rerun()

                if st.session_state.get(f"show_ans_vi_{default_vid}", False):
                    st.info(f"💡 Đáp án đầy đủ: **{word_for_en['vi']}**")

            # ---------------- PHẦN 2: VIỆT ➔ ANH ----------------
            with col_q2:
                st.markdown("#### 🇻🇳 ➔ 🇬🇧 Dịch sang Tiếng Anh")
                st.markdown(
                    f"""
                    <div style="background-color: #d97706; padding: 16px; border-radius: 10px; text-align: center; margin-bottom: 12px;">
                        <span style="font-size: 14px; color: #fef3c7; text-transform: uppercase; letter-spacing: 1px;">Nghĩa tiếng Việt:</span><br>
                        <span style="font-size: 26px; font-weight: bold; color: #ffffff;">{word_for_vi['vi']}</span><br>
                        <span style="font-size: 13px; color: #fef9c3;">(Gợi ý: <code>{word_for_vi['hint']}</code>)</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                with st.form(f"form_vi_en_{default_vid}_{st.session_state[k_vi_idx]}"):
                    user_en = st.text_input(
                        "Nhập từ tiếng Anh (Gõ xong nhấn Enter):", 
                        key=f"input_en_{default_vid}_{st.session_state[k_vi_idx]}"
                    )
                    sub_en = st.form_submit_button("Kiểm tra (Hoặc nhấn Enter)", use_container_width=True)
                    
                    if sub_en:
                        if check_vi_to_en_smart(user_en, word_for_vi['vi'], word_for_vi['en'], current_words):
                            st.session_state[f"correct_en_{default_vid}_{word_for_vi['en']}"] = True
                            st.success(f"🎉 Rất chuẩn xác! Từ bạn nhập: **{user_en.strip()}** đồng nghĩa hoàn toàn với **{word_for_vi['en']}**.")
                        else:
                            st.session_state[f"correct_en_{default_vid}_{word_for_vi['en']}"] = False
                            st.error(f"❌ Chưa đúng chính tả hoặc từ chưa khớp nghĩa với: **{word_for_vi['en']}**")

                if st.session_state.get(f"correct_en_{default_vid}_{word_for_vi['en']}", False):
                    if st.button(f"🗑️ Đã thuộc từ '{word_for_vi['en']}' - Xóa khỏi bài ôn", key=f"btn_del_done_vi_{word_for_vi['en']}", use_container_width=True):
                        remove_single_word(default_vid, word_for_vi["en"])
                        st.session_state.pop(f"correct_en_{default_vid}_{word_for_vi['en']}", None)
                        st.session_state.pop(f"show_ans_en_{default_vid}", None)
                        st.rerun()

                col_btn_r1, col_btn_r2 = st.columns(2)
                with col_btn_r1:
                    if st.button("👁️ Xem đáp án", key="btn_show_ans_en", use_container_width=True):
                        st.session_state[f"show_ans_en_{default_vid}"] = True

                with col_btn_r2:
                    if st.button("Từ tiếng Việt khác ➡️", key="btn_next_random_vi", use_container_width=True):
                        st.session_state[k_vi_idx] = random.randint(0, len(current_words) - 1)
                        st.session_state.pop(f"show_ans_en_{default_vid}", None)
                        st.session_state.pop(f"correct_en_{default_vid}_{word_for_vi['en']}", None)
                        st.rerun()

                if st.session_state.get(f"show_ans_en_{default_vid}", False):
                    st.info(f"💡 Đáp án từ tiếng Anh: **{word_for_vi['en']}**")
else:
    st.info("👋 Chào mừng bạn! Hãy thêm một video mới từ menu bên trái để bắt đầu học tập.")