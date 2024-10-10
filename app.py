import os
import time
import requests
import google.generativeai as genai
import csv
import streamlit as st
from concurrent.futures import ThreadPoolExecutor
import random
from dotenv import load_dotenv
from datetime import datetime
import json
import webbrowser
import calendar

# .env ファイルから環境変数を読み込む
load_dotenv()

# 環境変数から設定を読み込む
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RAKUTEN_APP_ID = os.getenv("RAKUTEN_APP_ID")
CATEGORY_DATA_PATH = os.getenv("CATEGORY_DATA_PATH")

genai.configure(api_key=GEMINI_API_KEY)

generation_config = {
    "temperature": 0.9,
    "top_p": 0.95,
    "top_k": 64,
    "max_output_tokens": 8192,
}

model = genai.GenerativeModel(
    model_name="gemini-1.5-pro-exp-0827",
    generation_config=generation_config,
)

@st.cache_data
def load_category_data(file_path):
    categories = {}
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter='\t')
        for row in reader:
            categories[row[1]] = row[0]
    return categories

def get_category_ids(user_request, categories, start_date, rice_ratio, bread_ratio, noodle_ratio):
    # 日本語の曜日名を取得
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    start_weekday = weekdays[start_date.weekday()]
    
    prompt = f"""
    以下は食材とそのカテゴリIDのリストです：
    {categories}
    
    ユーザーの要求: {user_request}
    開始日: {start_date.strftime('%Y-%m-%d')} ({start_weekday})
    主食の比重: ごはんもの {rice_ratio}%, パン {bread_ratio}%, 麺類 {noodle_ratio}%
    
    この要求と日付、曜日に合う食材・料理を20個選び、必ずそのカテゴリIDをカンマ区切りで出力してください。
    日付と曜日から季節や特別なイベント（例：お正月、クリスマス、ハロウィンなど）を考慮し、適切な食材を選んでください。
    また、それ以外は絶対に出力しないでください。
    出力形式: カテゴリID1,カテゴリID2,カテゴリID3
    """
    
    response = model.generate_content(prompt)
    return response.text.strip()

def get_recipe(category_id):
    url = "https://app.rakuten.co.jp/services/api/Recipe/CategoryRanking/20170426"
    params = {
        "applicationId": RAKUTEN_APP_ID,
        "categoryId": category_id.strip(),
        "format": "json",
        "elements": "recipeTitle,recipeUrl,recipeMaterial",
        "hits": 10  # 各カテゴリから最大10件のレシピを取得
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get('result', [])
    except requests.exceptions.RequestException as e:
        st.warning(f"カテゴリID {category_id} のAPIリクエストに失敗しました。")
        return []

@st.cache_data(ttl=3600)
def get_recipes(category_ids):
    all_recipes = []
    for category_id in category_ids.split(',')[:20]:  # 最大20カテゴリまで処理
        recipes = get_recipe(category_id)
        all_recipes.extend(recipes)
        time.sleep(1)  # 1秒間隔を空ける
    return all_recipes

def select_recipes(recipes, user_request, start_date, meal_types, rice_ratio, bread_ratio, noodle_ratio):
    recipes_info = "\n".join([
        f"{i+1}. {recipe['recipeTitle']} - 材料: {', '.join(recipe['recipeMaterial'])} - URL: {recipe.get('recipeUrl', 'URL不明')}"
        for i, recipe in enumerate(recipes)
    ])
    
    meal_types_str = ", ".join(meal_types)
    
    # 日本語の曜日名を取得
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    
    prompt = f"""
    ユーザーの要求: {user_request}
    開始日: {start_date.strftime('%Y-%m-%d')} ({weekdays[start_date.weekday()]})
    食事タイプ: {meal_types_str}
    主食の比重: ごはんもの {rice_ratio}%, パン {bread_ratio}%, 麺類 {noodle_ratio}%
    
    以下は、その要求に基づいて選ばれた食材から作れるレシピのリストです：
    {recipes_info}
    
    これらのレシピから、ユーザーの要求に最も適した1週間分の献立（{meal_types_str}）を作成してください。
    各食事について、条件をもとに1日ごとにステップバイステップでレシピを選定し、その理由、材料、URLを記載してください。
    主食の比重に従ってレシピを選択してください。
    開始日から季節や特別なイベント（例：お正月、クリスマス、ハロウィンなど）、さらに曜日も考慮し、適切なレシピを選んでください。
    最後に、1週間分の献立で必要な材料の総まとめを作成してください。

    出力形式:
    **[日付] ([曜日]):**
    """

    for meal_type in meal_types:
        prompt += f"""
    {meal_type}: [レシピNO].[レシピ名]
    理由: [選んだ理由]
    材料: [材料リスト]
    URL: [レシピのURL]
    """

    prompt += """
    ...
    1週間分の材料まとめ:
    
    **肉・魚:**
    [材料名]: [必要な量]

    **野菜:**
    [材料名]: [必要な量]

    **調味料など:**
    [材料名]: [必要な量]
    ...

    条件:
    - 必ず開始日から7日分の献立を出力してください。
    - 似た料理は絶対出さないでください。
    - 前日の残りなどは考慮しないでください。
    - 材料まとめでは、同じ材料を使用する場合はまとめて記載してください。ステップバイステップで、表記の重複がないことを確認してください。
    - 材料の量は、レシピに記載がない場合は適切な量を推定してください。「適量」という表現は禁止です。
    - 朝食は簡単に準備できるものを選んでください。
    - 夕食は朝昼に比べ手が込んでいるものを選んでください。
    - 主食そのもの、または主食にあうおかずなどを選択してください。
    - 絶対にサラダやスイーツ、味噌汁などを選ばないでください。
    - 開始日から季節や特別なイベント、曜日を考慮し、適切なレシピを選んでください。

    全レシピリスト:
    {recipes_info}
    """
    
    response = model.generate_content(prompt)
    return response.text.strip()

def parse_meal_plan(meal_plan, meal_types):
    lines = meal_plan.split('\n')
    parsed_plan = {}
    current_date = None
    current_meal = None
    materials_summary = []
    is_materials_summary = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("## 1週間分の材料まとめ:"):
            is_materials_summary = True
            continue

        if is_materials_summary:
            materials_summary.append(line)
            continue

        if line.startswith("**") and "(" in line and ")" in line:
            current_date = line.strip("*").split("(")[0].strip()
            parsed_plan[current_date] = {}
        elif any(line.startswith(f"{meal_type}:") for meal_type in meal_types):
            current_meal = line.split(":")[0]
            recipe = line.split(":", 1)[1].strip()
            parsed_plan[current_date][current_meal] = {"recipe": recipe, "reason": "", "materials": "", "url": ""}
        elif line.startswith("理由:"):
            if current_date and current_meal:
                parsed_plan[current_date][current_meal]["reason"] = line.split(":", 1)[1].strip()
        elif line.startswith("材料:"):
            if current_date and current_meal:
                parsed_plan[current_date][current_meal]["materials"] = line.split(":", 1)[1].strip()
        elif line.startswith("URL:"):
            if current_date and current_meal:
                parsed_plan[current_date][current_meal]["url"] = line.split(":", 1)[1].strip()

    return parsed_plan, materials_summary

def get_food_icon(meal_type):
    meal_icons = {
        "朝食": "fa-sun",
        "昼食": "fa-cloud-sun",
        "夕食": "fa-moon"
    }
    return meal_icons.get(meal_type, "fa-utensils")

def display_calendar(meal_plan):
    col1, col2, col3 = st.columns(3)
    
    for i, (date, meals) in enumerate(meal_plan.items()):
        if i % 3 == 0:
            col = col1
        elif i % 3 == 1:
            col = col2
        else:
            col = col3
        
        with col:
            st.subheader(date)
            for meal_type, meal_info in meals.items():
                icon = get_food_icon(meal_type)
                st.markdown(f"<i class='fas {icon}'></i> **{meal_type}**", unsafe_allow_html=True)
                st.write(meal_info['recipe'])
                if st.button(f"{date} {meal_type}の詳細を見る", key=f"{date}_{meal_type}"):
                    st.session_state.current_page = f"{date}_{meal_type}"

def display_meal_details(date, meal_type, meal_info):
    st.subheader(f"{date} - {meal_type}")
    st.write(f"**レシピ:** {meal_info['recipe']}")
    st.write(f"**理由:** {meal_info['reason']}")
    st.write(f"**材料:** {meal_info['materials']}")
    st.write(f"**URL:** [{meal_info['url']}]({meal_info['url']})")
    if st.button("カレンダーに戻る"):
        st.session_state.current_page = "calendar"

def save_meal_plan(meal_plan, materials_summary, save_path):
    data = {
        "meal_plan": meal_plan,
        "materials_summary": materials_summary
    }
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_meal_plan(file):
    content = file.getvalue().decode("utf-8")
    data = json.loads(content)
    return data["meal_plan"], data["materials_summary"]

def generate_html(meal_plan, materials_summary, html_path):
    html_content = """
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>1週間分の献立</title>
        <style>
            body { font-family: Arial, sans-serif; line-height: 1.6; padding: 20px; }
            h1, h2, h3 { color: #333; }
            .meal { margin-bottom: 20px; }
            .materials { background-color: #f4f4f4; padding: 10px; margin-top: 20px; }
        </style>
    </head>
    <body>
        <h1>1週間分の献立</h1>
    """

    for date, meals in meal_plan.items():
        html_content += f"<h2>{date}</h2>"
        for meal_type, details in meals.items():
            html_content += f"""
            <div class="meal">
                <h3>{meal_type}: {details['recipe']}</h3>
                <p><strong>理由:</strong> {details['reason']}</p>
                <p><strong>材料:</strong> {details['materials']}</p>
                <p><a href="{details['url']}" target="_blank">レシピを見る</a></p>
            </div>
            """

    html_content += "<h2>1週間分の材料まとめ</h2><div class='materials'>"
    for line in materials_summary:
        html_content += f"<p>{line}</p>"
    html_content += "</div></body></html>"

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

def main():
    st.set_page_config(page_title="AI主夫", layout="wide")
    
    # Font Awesome の CSS を追加
    st.markdown("""
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.1/css/all.min.css">
    <style>
    .fas { font-size: 24px; margin-right: 10px; }
    </style>
    """, unsafe_allow_html=True)
    
    st.title("AI主夫")
    st.write("あなたの要望に基づいて、1週間分のバランスの取れた献立を提案します。")

    categories = load_category_data(CATEGORY_DATA_PATH)

    user_request = st.text_input("1週間分の献立について、どのような要望がありますか？（例：野菜中心、和食メイン、簡単な料理など）")
    
    start_date = st.date_input("開始日を選択してください", min_value=datetime.now().date())
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    st.write(f"選択された開始日: {start_date.strftime('%Y-%m-%d')} ({weekdays[start_date.weekday()]})")
    
    meal_types = st.multiselect("食事の種類を選択してください", ["朝食", "昼食", "夕食"], default=["朝食", "昼食", "夕食"])

    # 主食の比重選択
    st.subheader("主食の比重")
    rice_ratio = st.slider("ごはんもの", 0, 100, 50, 10)
    bread_ratio = st.slider("パン", 0, 100, 25, 10)
    noodle_ratio = st.slider("麺類", 0, 100, 25, 10)

    if "meal_plan" not in st.session_state:
        st.session_state.meal_plan = None
        st.session_state.materials_summary = None
        st.session_state.current_page = "calendar"
        st.session_state.debug_info = {}

    if st.button("献立を作成", key="create_plan"):
        if user_request and meal_types:
            with st.spinner("献立を作成中...しばらくお待ちください（1-2分程度かかります）"):
                progress_bar = st.progress(0)
                
                progress_bar.progress(10)
                category_ids = get_category_ids(user_request, categories, start_date, rice_ratio, bread_ratio, noodle_ratio)
                st.session_state.debug_info['category_ids'] = category_ids
                
                progress_bar.progress(30)
                recipes = get_recipes(category_ids)
                st.session_state.debug_info['recipes'] = recipes
                
                if not recipes:
                    st.error("レシピを取得できませんでした。もう一度お試しください。")
                    return

                progress_bar.progress(50)
                meal_plan_text = select_recipes(recipes, user_request, start_date, meal_types, rice_ratio, bread_ratio, noodle_ratio)
                st.session_state.debug_info['meal_plan_text'] = meal_plan_text
                
                progress_bar.progress(80)
                st.session_state.meal_plan, st.session_state.materials_summary = parse_meal_plan(meal_plan_text, meal_types)
                st.session_state.debug_info['parsed_meal_plan'] = st.session_state.meal_plan
                st.session_state.debug_info['materials_summary'] = st.session_state.materials_summary
                
                progress_bar.progress(100)
                
                st.success("献立が完成しました！")
                st.session_state.current_page = "calendar"
        else:
            st.warning("要望を入力し、少なくとも1つの食事タイプを選択してください。")

    # 献立が生成された後に保存機能を表示
    if st.session_state.meal_plan:
        st.subheader("献立の保存")
        default_save_dir = r"C:\Users\b1019\recipe_service\保存"
        save_dir = st.text_input("保存先ディレクトリを入力してください:", value=default_save_dir)
        if st.button("献立を保存"):
            try:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                
                # JSONファイルの保存
                json_path = os.path.join(save_dir, f"meal_plan_{timestamp}.json")
                save_meal_plan(st.session_state.meal_plan, st.session_state.materials_summary, json_path)
                st.success(f"献立をJSONファイルとして {json_path} に保存しました。")

                # HTMLファイルの生成
                html_path = os.path.join(save_dir, f"meal_plan_{timestamp}.html")
                generate_html(st.session_state.meal_plan, st.session_state.materials_summary, html_path)
                st.success(f"HTMLファイルを {html_path} に生成しました。")
                
                if st.button("HTMLを開く", key="open_html"):
                    webbrowser.open(f"file://{os.path.abspath(html_path)}")
            except Exception as e:
                st.error(f"献立の保存中にエラーが発生しました: {str(e)}")
                st.error("ディレクトリの権限や空き容量を確認してください。")

    # 読み込み機能の強化（Streamlitの標準ファイルアップローダーを使用）
    st.subheader("保存した献立を読み込む")
    uploaded_file = st.file_uploader("JSONファイルをアップロードしてください", type=["json"])
    
    if uploaded_file is not None:
        try:
            st.session_state.meal_plan, st.session_state.materials_summary = load_meal_plan(uploaded_file)
            st.success("献立を読み込みました。")
            st.session_state.current_page = "calendar"
        except Exception as e:
            st.error(f"献立の読み込みに失敗しました: {str(e)}")

    # 献立の表示（カレンダーまたは詳細）
    if st.session_state.meal_plan:
        if st.session_state.current_page == "calendar":
            display_calendar(st.session_state.meal_plan)
            st.subheader("1週間分の材料まとめ")
            for line in st.session_state.materials_summary:
                st.write(line)
        elif "_" in st.session_state.current_page:
            date, meal_type = st.session_state.current_page.split("_")
            if date in st.session_state.meal_plan and meal_type in st.session_state.meal_plan[date]:
                display_meal_details(date, meal_type, st.session_state.meal_plan[date][meal_type])
            else:
                st.error(f"{date}の{meal_type}の情報が見つかりません。")
                if st.button("カレンダーに戻る"):
                    st.session_state.current_page = "calendar"

    # デバッグ情報の表示
    st.subheader("デバッグ情報")
    if st.session_state.debug_info:
        st.write("選択されたカテゴリID:")
        st.code(st.session_state.debug_info['category_ids'])
        
        st.write("取得されたレシピ数:")
        st.write(len(st.session_state.debug_info['recipes']))
        
        st.write("生成された献立テキスト:")
        st.code(st.session_state.debug_info['meal_plan_text'])
        
        st.write("解析された献立データ:")
        st.json(st.session_state.debug_info['parsed_meal_plan'])
        
        st.write("材料まとめ:")
        for line in st.session_state.debug_info['materials_summary']:
            st.write(line)

    st.sidebar.title("使い方")
    st.sidebar.write("""
    1. 要望を入力欄に記入してください。
    2. 開始日を選択してください。
    3. 食事の種類を選択してください。
    4. 主食の比重を調整してください。
    5. '献立を作成'ボタンをクリックします。
    6. 生成された献立を保存する場合は、保存先を指定して'献立を保存'ボタンをクリックしてください。
    7. カレンダー形式で献立が表示されます。
    8. 各日付の詳細を見るには、対応するボタンをクリックしてください。
    9. 保存した献立を読み込むには、ファイルパスを指定して'献立を読み込む'ボタンをクリックしてください。
    """)

if __name__ == "__main__":
    main()