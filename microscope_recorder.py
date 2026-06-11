"""
顕微鏡録画アプリ - 舌野クリニック
依存: pip install opencv-python pillow
"""

import cv2
import tkinter as tk
from tkinter import messagebox
import threading
import time
import os
import re
from datetime import datetime
from PIL import Image, ImageTk

# ── 設定 ────────────────────────────────────────────────
SAVE_DIR = r"C:\Users\shita\Videos\PC04顕微鏡画像"   # 保存先フォルダ（変更可）
CAMERA_INDEX = 0                      # USBカメラのインデックス（通常0）
PREVIEW_FPS = 15                      # プレビューFPS
RECORD_FPS = 15                       # 録画FPS
PREVIEW_SIZE = (960, 540)            # プレビュー表示サイズ（画面表示用）
CAPTURE_SIZE = (1920, 1080)          # カメラ取得・録画解像度

SPECIMEN_TYPES = [
    "帯下",
    "精子",
    "尿沈渣",
    "血液",
    "その他",
]

DEFAULT_DURATION = 30   # デフォルト録画秒数


# ── ファイル名生成 ────────────────────────────────────────
INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_for_filename(text: str) -> str:
    return INVALID_FILENAME_CHARS.sub("_", text).strip()


def generate_filename(patient_id: str, specimen: str, save_dir: str, ext: str = "mp4") -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    safe_id = sanitize_for_filename(patient_id)
    safe_specimen = sanitize_for_filename(specimen)
    base = f"{date_str}_{safe_id}_{safe_specimen}"
    # 連番を検索して付与
    existing = [
        f for f in os.listdir(save_dir)
        if f.startswith(base) and f.endswith(f".{ext}")
    ]
    nums = []
    for f in existing:
        m = re.search(rf"_(\d+)\.{ext}$", f)
        if m:
            nums.append(int(m.group(1)))
    next_num = max(nums) + 1 if nums else 1
    return os.path.join(save_dir, f"{base}_{next_num:03d}.{ext}")


# ── メインアプリ ─────────────────────────────────────────
class MicroscopeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("顕微鏡録画システム")
        self.root.configure(bg="#1a1a2e")
        self.root.resizable(False, False)

        self.cap = None
        self.recording = False
        self.writer = None
        self.writer_lock = threading.Lock()
        self.last_frame = None
        self.frame_lock = threading.Lock()
        self.preview_thread = None
        self.record_timer = None
        self.remaining = 0
        self.preview_active = False

        os.makedirs(SAVE_DIR, exist_ok=True)
        self._build_ui()
        self._start_preview()

    # ── UI構築 ───────────────────────────────────────────
    def _build_ui(self):
        root = self.root

        # ── タイトルバー
        header = tk.Frame(root, bg="#0f3460", pady=8)
        header.pack(fill="x")
        tk.Label(
            header, text="🔬  顕微鏡録画システム",
            font=("Meiryo UI", 14, "bold"),
            fg="#e0e0e0", bg="#0f3460"
        ).pack()

        # ── メインエリア
        main = tk.Frame(root, bg="#1a1a2e", padx=16, pady=12)
        main.pack(fill="both")

        # 左: プレビュー
        left = tk.Frame(main, bg="#1a1a2e")
        left.grid(row=0, column=0, rowspan=4, padx=(0, 16))

        self.canvas = tk.Canvas(left, width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1],
                                bg="#000", highlightthickness=2,
                                highlightbackground="#16213e")
        self.canvas.pack()

        self.rec_indicator = tk.Label(left, text="", bg="#1a1a2e",
                                      fg="#ff4444", font=("Meiryo UI", 10, "bold"))
        self.rec_indicator.pack(pady=(4, 0))

        # 右: コントロールパネル
        right = tk.Frame(main, bg="#1a1a2e")
        right.grid(row=0, column=1, sticky="n")

        def section_label(parent, text):
            tk.Label(parent, text=text, font=("Meiryo UI", 9),
                     fg="#7a8fa6", bg="#1a1a2e").pack(anchor="w", pady=(10, 2))

        def entry_field(parent):
            e = tk.Entry(parent, font=("Meiryo UI", 13),
                         bg="#16213e", fg="#e0e0e0",
                         insertbackground="#e0e0e0",
                         relief="flat", bd=0, width=20,
                         highlightthickness=1,
                         highlightbackground="#0f3460",
                         highlightcolor="#e94560")
            e.pack(ipady=6, fill="x")
            return e

        # 患者ID
        section_label(right, "患者ID")
        self.id_var = tk.StringVar()
        self.id_entry = entry_field(right)
        self.id_entry.config(textvariable=self.id_var)

        # 検体種別
        section_label(right, "検体種別")
        self.specimen_var = tk.StringVar(value=SPECIMEN_TYPES[0])
        spec_frame = tk.Frame(right, bg="#1a1a2e")
        spec_frame.pack(fill="x")
        for sp in SPECIMEN_TYPES:
            rb = tk.Radiobutton(
                spec_frame, text=sp, variable=self.specimen_var, value=sp,
                font=("Meiryo UI", 10),
                fg="#c0c0c0", bg="#1a1a2e",
                selectcolor="#0f3460",
                activebackground="#1a1a2e",
                activeforeground="#e94560"
            )
            rb.pack(anchor="w")

        # 録画時間
        section_label(right, "録画時間（秒）")
        dur_frame = tk.Frame(right, bg="#1a1a2e")
        dur_frame.pack(fill="x")
        self.duration_var = tk.IntVar(value=DEFAULT_DURATION)
        for sec in [15, 30, 60, 120]:
            b = tk.Radiobutton(
                dur_frame, text=f"{sec}秒", variable=self.duration_var, value=sec,
                font=("Meiryo UI", 10),
                fg="#c0c0c0", bg="#1a1a2e",
                selectcolor="#0f3460",
                activebackground="#1a1a2e",
                activeforeground="#e94560"
            )
            b.pack(side="left", padx=4)

        # カスタム秒数
        custom_frame = tk.Frame(right, bg="#1a1a2e")
        custom_frame.pack(fill="x", pady=(4, 0))
        tk.Label(custom_frame, text="カスタム:", font=("Meiryo UI", 9),
                 fg="#7a8fa6", bg="#1a1a2e").pack(side="left")
        self.custom_dur = tk.Entry(custom_frame, font=("Meiryo UI", 11),
                                   bg="#16213e", fg="#e0e0e0",
                                   insertbackground="#e0e0e0",
                                   width=5, relief="flat",
                                   highlightthickness=1,
                                   highlightbackground="#0f3460")
        self.custom_dur.pack(side="left", ipady=4, padx=4)
        tk.Label(custom_frame, text="秒", font=("Meiryo UI", 9),
                 fg="#7a8fa6", bg="#1a1a2e").pack(side="left")

        # カウントダウン表示
        self.countdown_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.countdown_var,
                 font=("Meiryo UI", 22, "bold"),
                 fg="#e94560", bg="#1a1a2e").pack(pady=(16, 4))

        # ファイル名プレビュー
        self.fname_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.fname_var,
                 font=("Meiryo UI", 8),
                 fg="#5a6a7a", bg="#1a1a2e",
                 wraplength=260, justify="left").pack(anchor="w")

        self.id_var.trace_add("write", lambda *_: self._update_fname_preview())
        self.specimen_var.trace_add("write", lambda *_: self._update_fname_preview())
        self._update_fname_preview()

        # 録画ボタン
        btn_frame = tk.Frame(right, bg="#1a1a2e")
        btn_frame.pack(pady=16, fill="x")

        self.rec_btn = tk.Button(
            btn_frame,
            text="⏺  録画開始",
            font=("Meiryo UI", 13, "bold"),
            bg="#e94560", fg="white",
            activebackground="#c73652", activeforeground="white",
            relief="flat", bd=0, cursor="hand2",
            command=self._toggle_record
        )
        self.rec_btn.pack(fill="x", ipady=10)

        self.photo_btn = tk.Button(
            btn_frame,
            text="📷  静止画撮影",
            font=("Meiryo UI", 13, "bold"),
            bg="#0f3460", fg="white",
            activebackground="#16213e", activeforeground="white",
            relief="flat", bd=0, cursor="hand2",
            command=self._capture_photo
        )
        self.photo_btn.pack(fill="x", ipady=10, pady=(8, 0))

        # 撮影ステータス表示
        self.capture_status_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.capture_status_var,
                 font=("Meiryo UI", 9, "bold"),
                 fg="#4caf50", bg="#1a1a2e").pack(anchor="w", pady=(4, 0))

        # 保存フォルダ表示
        tk.Label(right, text=f"保存先: {SAVE_DIR}", font=("Meiryo UI", 8),
                 fg="#3a4a5a", bg="#1a1a2e").pack(anchor="w")

    def _update_fname_preview(self):
        pid = sanitize_for_filename(self.id_var.get().strip()) or "ID"
        sp = self.specimen_var.get()
        date_str = datetime.now().strftime("%Y%m%d")
        safe_sp = sanitize_for_filename(sp)
        base = f"{date_str}_{pid}_{safe_sp}_001"
        self.fname_var.set(f"例: {base}.mp4 / {base}.jpg")

    # ── カメラプレビュー ─────────────────────────────────
    def _start_preview(self):
        self.cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            messagebox.showerror("カメラエラー",
                                 f"カメラ(index={CAMERA_INDEX})を開けません。\n"
                                 "USBキャプチャデバイスを確認してください。")
            self.cap.release()
            self.cap = None
            self.rec_btn.config(state="disabled")
            self.photo_btn.config(state="disabled")
            return
        # MJPG指定でUSB帯域不足による1080pキャプチャの遅延・カクつきを回避
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_SIZE[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_SIZE[1])
        self.preview_active = True
        self.preview_thread = threading.Thread(target=self._preview_loop, daemon=True)
        self.preview_thread.start()

    def _preview_loop(self):
        delay = 1.0 / PREVIEW_FPS
        while self.preview_active:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(delay)
                continue
            with self.frame_lock:
                self.last_frame = frame
            with self.writer_lock:
                if self.recording and self.writer:
                    self.writer.write(frame)
            preview_frame = cv2.resize(frame, PREVIEW_SIZE)
            frame_rgb = cv2.cvtColor(preview_frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
            # PhotoImageの生成・キャンバス更新はメインスレッドで行う
            self.canvas.after(0, self._update_canvas, img)
            time.sleep(delay)

    def _update_canvas(self, img):
        imgtk = ImageTk.PhotoImage(image=img)
        self.canvas._imgtk = imgtk  # GC防止
        if not hasattr(self, "_img_id"):
            self._img_id = self.canvas.create_image(0, 0, anchor="nw", image=imgtk)
        else:
            self.canvas.itemconfig(self._img_id, image=imgtk)

    # ── 静止画撮影 ───────────────────────────────────────
    def _capture_photo(self):
        if self.cap is None or not self.cap.isOpened():
            messagebox.showerror("カメラエラー", "カメラが利用できないため撮影できません。")
            return

        patient_id = self.id_var.get().strip()
        if not patient_id:
            messagebox.showwarning("入力エラー", "患者IDを入力してください。")
            return

        with self.frame_lock:
            frame = self.last_frame

        if frame is None:
            messagebox.showerror("撮影エラー", "映像を取得できませんでした。")
            return

        filepath = generate_filename(patient_id, self.specimen_var.get(), SAVE_DIR, ext="jpg")
        if not cv2.imwrite(filepath, frame):
            messagebox.showerror("撮影エラー",
                                 "画像を保存できませんでした。\n"
                                 "保存先フォルダの権限や空き容量を確認してください。")
            return

        self._flash_canvas()
        fname = os.path.basename(filepath)
        self.capture_status_var.set(f"📷 保存しました: {fname}")
        self.root.after(3000, lambda: self.capture_status_var.set(""))

    def _flash_canvas(self):
        self.canvas.config(highlightbackground="#ffffff")
        self.root.after(120, lambda: self.canvas.config(highlightbackground="#16213e"))

    # ── 録画制御 ─────────────────────────────────────────
    def _toggle_record(self):
        if self.recording:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self):
        if self.cap is None or not self.cap.isOpened():
            messagebox.showerror("カメラエラー", "カメラが利用できないため録画できません。")
            return

        patient_id = self.id_var.get().strip()
        if not patient_id:
            messagebox.showwarning("入力エラー", "患者IDを入力してください。")
            return

        # 録画時間の取得
        custom = self.custom_dur.get().strip()
        if custom:
            try:
                duration = int(custom)
                if duration <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("入力エラー", "カスタム秒数は正の整数で入力してください。")
                return
        else:
            duration = self.duration_var.get()

        filepath = generate_filename(patient_id, self.specimen_var.get(), SAVE_DIR)
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(filepath, fourcc, RECORD_FPS, (w, h))
        if not writer.isOpened():
            writer.release()
            messagebox.showerror("録画エラー",
                                 "録画ファイルを作成できませんでした。\n"
                                 "保存先フォルダの権限や空き容量を確認してください。")
            return

        with self.writer_lock:
            self.writer = writer
            self.recording = True
        self.remaining = duration
        self.rec_btn.config(text="⏹  録画停止", bg="#444")
        self.rec_indicator.config(text="● REC")
        self._tick(filepath)

    def _tick(self, filepath):
        if not self.recording:
            return
        self.countdown_var.set(f"{self.remaining}秒")
        if self.remaining <= 0:
            self._stop_record(filepath=filepath, auto=True)
            return
        self.remaining -= 1
        self.record_timer = self.root.after(1000, self._tick, filepath)

    def _stop_record(self, filepath="", auto=False):
        self.recording = False
        if self.record_timer:
            self.root.after_cancel(self.record_timer)
            self.record_timer = None
        with self.writer_lock:
            if self.writer:
                self.writer.release()
                self.writer = None
        self.rec_btn.config(text="⏺  録画開始", bg="#e94560")
        self.rec_indicator.config(text="")
        self.countdown_var.set("")
        if auto and filepath:
            fname = os.path.basename(filepath)
            messagebox.showinfo("録画完了", f"保存しました:\n{fname}")

    # ── 終了処理 ─────────────────────────────────────────
    def on_close(self):
        self.preview_active = False
        self.recording = False
        if self.preview_thread:
            self.preview_thread.join(timeout=1.0)
        with self.writer_lock:
            if self.writer:
                self.writer.release()
                self.writer = None
        if self.cap:
            self.cap.release()
        self.root.destroy()


# ── エントリーポイント ───────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app = MicroscopeApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
