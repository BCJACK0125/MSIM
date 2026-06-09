import os
import re
import json
import smtplib
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def parse_esun_html():
    target_url = "https://www.esunbank.com/zh-tw/personal/credit-card/discount/shopInfo?sno=2100_06"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    print("正在下載網頁原始碼...")
    response = requests.get(target_url, headers=headers)
    response.encoding = 'utf-8'
    
    if response.status_code != 200:
        print(f"網頁連線失敗: {response.status_code}")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    title_tags = soup.find_all('p', class_='pt-3')
    activity_list = []
    
    for p_tag in title_tags:
        title = p_tag.get_text(strip=True)
        if not title or "活動" not in title: 
            continue
        
        ul_tag = p_tag.find_next_sibling('ul', class_='greenBulletList')
        if not ul_tag:
            continue
            
        li_elements = ul_tag.find_all('li')
        all_text_combined = " ".join([li.get_text(strip=True) for li in li_elements])
        
        # 擷取時間與名額
        time_pattern = r'\d{4}/\d{1,2}/\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:~\d{4}/\d{1,2}/\d{1,2}\s+\d{2}:\d{2}:\d{2})?'
        times = re.findall(time_pattern, all_text_combined)
        registration_time = " / ".join(times) if times else "需見內文或免登錄"
        
        quota_pattern = r'(?:限量|名額|登錄)[^，。\n]*?([\d,]+名)'
        quotas = re.findall(quota_pattern, all_text_combined)
        quota_limit = " / ".join(list(set(quotas))) if quotas else "未明確標示限額"
        
        benefits = []
        for li in li_elements:
            text = li.get_text(strip=True)
            if any(keyword in text for keyword in ['回饋', '折', '點', '上限', '適用']):
                if "登錄辦法" not in text:
                    benefits.append(f"• {text}")
        benefits_summary = "<br>".join(benefits)

        activity_list.append({
            "name": title,
            "quota": quota_limit,
            "time": registration_time,
            "benefits": benefits_summary
        })
        
    return activity_list

def send_email(activities):
    # 從環境變數中讀取 GitHub Secrets 的設定
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    
    if not gmail_user or not gmail_password:
        print("未偵測到 Gmail 憑證環境變數，跳過發信。")
        return

    # 建立郵件本體
    msg = MIMEMultipart()
    msg['From'] = gmail_user
    msg['To'] = gmail_user # 寄給自己
    msg['Subject'] = f"【自動通知】玉山銀行信用卡優惠定時回報 ({datetime.now().strftime('%m/%d')})"

    # 製作漂亮的 HTML 表格
    html_content = f"""
    <html>
    <head>
        <style>
            table {{ border-collapse: collapse; width: 100%; font-family: sans-serif; }}
            th, td {{ border: 1px solid #dddddd; text-align: left; padding: 10px; }}
            th {{ background-color: #00a19b; color: white; }}
            tr:nth-child(even) {{ background-color: #f9f9f9; }}
            .highlight {{ color: #f8524c; font-weight: bold; }}
        </style>
    </head>
    <body>
        <h2>🎯 目前最新玉山刷卡活動一覽</h2>
        <p>更新時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <table>
            <tr>
                <th>活動名稱</th>
                <th>限額數量</th>
                <th>登錄時間</th>
                <th>優惠核心摘要</th>
            </tr>
    """
    
    for act in activities:
        html_content += f"""
            <tr>
                <td><b>{act['name']}</b></td>
                <td class="highlight">{act['quota']}</td>
                <td>{act['time']}</td>
                <td style="font-size: 13px;">{act['benefits']}</td>
            </tr>
        """
        
    html_content += """
        </table>
        <br>
        <p>備註：本信件由 GitHub Actions 爬蟲機器人自動發送。</p>
    </body>
    </html>
    """
    
    msg.attach(MIMEText(html_content, 'html', 'utf-8'))

    try:
        # 連線到 Gmail SMTP 伺服器 (使用 TLS 加密)
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(gmail_user, gmail_password)
        server.send_message(msg)
        server.quit()
        print("電子郵件已成功寄出！")
    except Exception as e:
        print(f"發信失敗: {e}")

if __name__ == "__main__":
    data = parse_esun_html()
    if data:
        # 1. 執行發信
        send_email(data)
        
        # 2. 同時保留轉成 JSON 存檔的邏輯（方便 Git 追蹤歷史紀錄）
        final_output = {
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": data
        }
        with open("esun_discounts.json", "w", encoding="utf-8") as f:
            json.dump(final_output, f, ensure_ascii=False, indent=4)