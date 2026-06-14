import requests
import pandas as pd
from datetime import datetime
import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from bs4 import BeautifulSoup

# 設定：主要通貨ペアの名称マッピング（Financial Tradersレポート用）
TFF_MAPPING = {
    "JPY": "JAPANESE YEN",
    "EUR": "EURO CURRENCY",
    "GBP": "BRITISH POUND",
    "AUD": "AUSTRALIAN DOLLAR",
    "CAD": "CANADIAN DOLLAR"
}

# === メール送信用設定（環境変数から取得推奨） ===
# 例: export MAIL_USER="xxxx@gmail.com", export MAIL_PASS="xxxx"
MAIL_CONFIG = {
    "smtp_server": os.getenv("MAIL_SMTP_SERVER", "smtp.gmail.com"),
    "smtp_port": int(os.getenv("MAIL_SMTP_PORT", "587")),
    "user": os.getenv("MAIL_USER", ""),
    "password": os.getenv("MAIL_PASS", ""),  # Gmailはアプリパスワード
    "from_addr": os.getenv("MAIL_FROM", os.getenv("MAIL_USER", "")),
    "to_addrs": [a.strip() for a in os.getenv("MAIL_TO", "").split(",") if a.strip()],
}

def send_email_with_attachment(file_path: str) -> bool:
    """
    単一のファイル（レポート）を添付してメール送信する。
    添付対象は file_path 引数で渡された1つのみ。
    """
    # 必須設定のチェック
    if not (MAIL_CONFIG["user"] and MAIL_CONFIG["password"] and MAIL_CONFIG["to_addrs"]):
        print("メール設定が不完全です (MAIL_USER / MAIL_PASS / MAIL_TO を確認)。")
        return False

    if not file_path or not os.path.exists(file_path):
        print(f"送信対象のファイルが存在しません: {file_path}")
        return False

    # 単一ファイルに限定（念のためディレクトリ指定や複数添付を拒否）
    if os.path.isdir(file_path):
        print(f"ディレクトリは送信できません: {file_path}")
        return False

    filename = os.path.basename(file_path)

    # メール本文作成
    msg = MIMEMultipart()
    msg["Subject"] = f"🐋 クジラ便乗シグナル・レポート {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = MAIL_CONFIG["from_addr"]
    msg["To"] = ", ".join(MAIL_CONFIG["to_addrs"])

    body = (
        "お疲れ様です。\n"
        "本日のクジラ便乗シグナル・レポートを添付いたします。\n\n"
        f"添付ファイル: {filename}\n"
        f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # ★ 単一ファイルのみを添付
    try:
        with open(file_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=filename)
            part["Content-Disposition"] = f'attachment; filename="{filename}"'
            msg.attach(part)
    except Exception as e:
        print(f"添付ファイル読み込みエラー: {e}")
        return False

    # SMTP送信
    try:
        with smtplib.SMTP(MAIL_CONFIG["smtp_server"], MAIL_CONFIG["smtp_port"], timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(MAIL_CONFIG["user"], MAIL_CONFIG["password"])
            server.sendmail(
                MAIL_CONFIG["from_addr"],
                MAIL_CONFIG["to_addrs"],
                msg.as_string()
            )
        print(f"✅ メール送信成功: {filename} → {MAIL_CONFIG['to_addrs']}")
        return True
    except Exception as e:
        print(f"❌ メール送信エラー: {e}")
        return False


# === 以下、元のロジック（get_latest_cot_data / get_sentiment_data / calculate_signals）は変更なし ===

def get_latest_cot_data():
    """CFTCの閲覧用ページ（HTML）から最新のTFFデータをスクレイピング"""
    url = "https://www.cftc.gov/dea/futures/financial_lf.htm"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            text = response.text
            sections = text.split("---------------------------------------------------------------------------------")
            results = []
            for section in sections:
                market_match = re.search(r'([A-Z\s]+) - CHICAGO MERCANTILE EXCHANGE', section)
                if market_match:
                    market_name = market_match.group(1).strip()
                    lev_match = re.search(r'Leveraged Funds\s+\|\s+([\d,]+)\|\s+([\d,]+)\|', section)
                    if lev_match:
                        long_val = int(lev_match.group(1).replace(',', ''))
                        short_val = int(lev_match.group(2).replace(',', ''))
                        results.append({
                            "Market": market_name,
                            "Long": long_val,
                            "Short": short_val
                        })
            return pd.DataFrame(results)
        return None
    except Exception as e:
        print(f"COTデータ取得エラー: {e}")
        return None


def get_sentiment_data():
    """Myfxbookからセンチメントを取得"""
    url = "https://www.myfxbook.com/community/outlook"
    headers = {"User-Agent": "Mozilla/5.0"}
    data = []
    try:
        response = requests.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(response.content, 'html.parser')
        text = soup.get_text(separator=' ')
        for sym in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]:
            pattern = rf'{sym}.*?Short\s+(\d+)%.*?Long\s+(\d+)%'
            match = re.search(pattern, text, re.DOTALL)
            if match:
                data.append({
                    "Symbol": sym,
                    "Retail_Short": int(match.group(1)),
                    "Retail_Long": int(match.group(2))
                })
        return data
    except Exception as e:
        print(f"センチメント取得エラー: {e}")
        return []


def calculate_signals(cot_df, sentiment_list):
    signals = []
    if cot_df is None or cot_df.empty:
        return pd.DataFrame()
    for sent in sentiment_list:
        symbol = sent['Symbol']
        base_curr = symbol[:3]
        target_curr = base_curr if base_curr != "USD" else symbol[3:]
        target_name = TFF_MAPPING.get(target_curr)
        if target_name:
            mask = cot_df['Market'].str.contains(target_name, case=False, na=False)
            if mask.any():
                cot_row = cot_df[mask].iloc[0]
                whale_long = float(cot_row['Long'])
                whale_short = float(cot_row['Short'])
                whale_ratio = (whale_long / (whale_long + whale_short)) * 100
                whale_bias = "Bullish" if whale_ratio > 55 else ("Bearish" if whale_ratio < 45 else "Neutral")
                retail_bias = "Bearish" if sent['Retail_Long'] > 60 else ("Bullish" if sent['Retail_Short'] > 60 else "Neutral")
                status = "Strong Buy" if whale_bias == "Bullish" and retail_bias == "Bullish" else \
                         "Strong Sell" if whale_bias == "Bearish" and retail_bias == "Bearish" else \
                         "Wait"
                if symbol == "USDJPY":
                    if status == "Strong Buy": status = "Strong Sell"
                    elif status == "Strong Sell": status = "Strong Buy"
                signals.append({
                    "ペア": symbol,
                    "クジラ(Long%)": f"{whale_ratio:.1f}%",
                    "大衆(Long%)": f"{sent['Retail_Long']}%",
                    "判定": status
                })
    return pd.DataFrame(signals)


def generate_report(signal_df):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = f"# 🐋 クジラ便乗シグナル・レポート\n生成日時: {now}\n\n"
    report += "## 📊 総合シグナル判定\n"
    if not signal_df.empty:
        report += signal_df.to_markdown(index=False) + "\n\n"
    else:
        report += "現在、有効なシグナルはありません。週末やデータ更新待ちの可能性があります。\n\n"
    report += "## 💡 判定ロジック\n- **Strong Buy**: クジラが買い、大衆が売っている（踏み上げ期待）\n- **Strong Sell**: クジラが売り、大衆が買っている（投げ売り期待）\n\n"
    report += "---\n*このレポートは自動生成されました。*"
    path = f"whale_signal_{datetime.now().strftime('%Y%m%d')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    return path  # ★ 生成したファイルパスを返す（元と同じ）


if __name__ == "__main__":
    print("データ取得開始...")
    cot_data = get_latest_cot_data()
    sentiment = get_sentiment_data()
    print(f"COTデータ取得結果: {'成功' if cot_data is not None else '失敗'}")
    print(f"センチメント取得数: {len(sentiment)}")

    signal_df = calculate_signals(cot_data, sentiment)
    report_path = generate_report(signal_df)
    print(f"シグナルレポート生成完了: {report_path}")

    # ★ 新規作成した1ファイルのみをメール送信
    send_email_with_attachment(report_path)
