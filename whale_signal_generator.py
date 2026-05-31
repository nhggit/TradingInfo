import requests
import pandas as pd
from datetime import datetime
import os
import re
from bs4 import BeautifulSoup

# 設定：主要通貨ペアの名称マッピング（Financial Tradersレポート用）
TFF_MAPPING = {
    "JPY": "JAPANESE YEN",
    "EUR": "EURO CURRENCY",
    "GBP": "BRITISH POUND",
    "AUD": "AUSTRALIAN DOLLAR",
    "CAD": "CANADIAN DOLLAR"
}

def get_latest_cot_data():
    """CFTCの閲覧用ページ（HTML）から最新のTFFデータをスクレイピング"""
    # 最新のFinancial Traders (Short Form) レポートのURL
    url = "https://www.cftc.gov/dea/futures/financial_lf.htm"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            text = response.text
            # 各通貨ペアのセクションを分割
            sections = text.split("---------------------------------------------------------------------------------")
            results = []
            
            for section in sections:
                # 銘柄名を探す
                market_match = re.search(r'([A-Z\s]+) - CHICAGO MERCANTILE EXCHANGE', section)
                if market_match:
                    market_name = market_match.group(1).strip()
                    # Leveraged Funds (投機筋) の Long/Short を探す
                    # フォーマット例: "Leveraged Funds  |  12,345|  6,789|"
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
        # 全てのテキストから通貨ペアと%を抽出
        text = soup.get_text(separator=' ')
        for sym in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]:
            # 例: "EURUSD ... Short 40% ... Long 60%"
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
    if cot_df is None or cot_df.empty: return pd.DataFrame()
    
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
    return path

if __name__ == "__main__":
    print("データ取得開始...")
    cot_data = get_latest_cot_data()
    sentiment = get_sentiment_data()
    
    print(f"COTデータ取得結果: {'成功' if cot_data is not None else '失敗'}")
    print(f"センチメント取得数: {len(sentiment)}")
    
    signal_df = calculate_signals(cot_data, sentiment)
    path = generate_report(signal_df)
    print(f"シグナルレポート生成完了: {path}")
