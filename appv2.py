import os
import time
import asyncio
import aiohttp
from asyncio import Semaphore
import google.generativeai as genai
import csv
import streamlit as st
import random
from dotenv import load_dotenv
from datetime import datetime
import json
import webbrowser
import calendar
import re

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

# 1秒に1リクエストの制限を守るためのセマフォ
API_SEMAPHORE = Semaphore(1)

@st.cache_data
def load_category_data(file_path):
    categories = {}
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter='\t')
        for row in reader:
            categories[row[1]] = row[0]
    return categories

def get_user_attributes():
    st.subheader("ユーザー情報")
    gender = st.selectbox("性別", ["男性", "女性", "その他"])
    age = st.number_input("年齢", min_value=1, max_value=120, value=30)
    height = st.number_input("身長 (cm)", min_value=50, max_value=250, value=170)
    weight = st.number_input("体重 (kg)", min_value=20, max_value=300, value=60)
    activity_level = st.selectbox("活動レベル", ["低い", "普通", "高い"])
    dietary_preferences = st.multiselect("食事の注意点", ["カロリー制限", "塩分制限", "糖質制限", "ベジタリアン", "ビーガン"])
    return {
        "gender": gender,
        "age": age,
        "height": height,
        "weight": weight,
        "activity_level": activity_level,
        "dietary_preferences": dietary_preferences
    }

def estimate_calorie_needs(user_attributes):
    if user_attributes["gender"] == "男性":
        bmr = 88.362 + (13.397 * user_attributes["weight"]) + (4.799 * user_attributes["height"]) - (5.677 * user_attributes["age"])
    else:
        bmr = 447.593 + (9.247 * user_attributes["weight"]) + (3.098 * user_attributes["height"]) - (4.330 * user_attributes["age"])
    
    activity_factors = {"低い": 1.2, "普通": 1.55, "高い": 1.9}
    daily_calories = bmr * activity_factors[user_attributes["activity_level"]]
    return daily_calories

def get_category_ids(user_request, categories, start_date, rice_ratio, bread_ratio, noodle_ratio):
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

def extract_number(string):
    match = re.search(r'\d+', string)
    if match:
        return int(match.group())
    return 0

async def fetch_recipe(session, category_id):
    recipe_url = "https://app.rakuten.co.jp/services/api/Recipe/CategoryRanking/20170426"
    recipe_params = {
        "applicationId": RAKUTEN_APP_ID,
        "categoryId": category_id.strip(),
        "format": "json",
        "elements": "recipeTitle,recipeUrl,recipeMaterial,recipeCost"
    }
    
    async with API_SEMAPHORE:  # セマフォを使用してリクエストを制御
        try:
            async with session.get(recipe_url, params=recipe_params) as response:
                response.raise_for_status()
                recipe_data = await response.json()
                
                recipes = recipe_data.get('result', [])
                
                await asyncio.sleep(1)  # 1秒待機
                return recipes
        except Exception as e:
            print(f"カテゴリID {category_id} のAPIリクエストに失敗しました: {str(e)}")
            return []

async def get_recipes_async(category_ids):
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_recipe(session, category_id) for category_id in category_ids.split(',')[:20]]
        results = await asyncio.gather(*tasks)
        return [recipe for sublist in results for recipe in sublist]

@st.cache_data(ttl=3600)
def get_recipes(category_ids):
    return asyncio.run(get_recipes_async(category_ids))

def select_recipes_with_estimation(recipes, user_request, start_date, meal_types, rice_ratio, bread_ratio, noodle_ratio, user_attributes):
    daily_calorie_needs = estimate_calorie_needs(user_attributes)
    recipes_info = "\n".join([
        f"{i+1}. {recipe['recipeTitle']} - 材料: {', '.join(recipe['recipeMaterial'])} - URL: {recipe.get('recipeUrl', 'URL不明')}"
        for i, recipe in enumerate(recipes)
    ])
    
    meal_types_str = ", ".join(meal_types)
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    
    prompt = f"""
    ユーザーの要求: {user_request}
    開始日: {start_date.strftime('%Y-%m-%d')} ({weekdays[start_date.weekday()]})
    食事タイプ: {meal_types_str}
    主食の比重: ごはんもの {rice_ratio}%, パン {bread_ratio}%, 麺類 {noodle_ratio}%
    1日の推奨カロリー摂取量: {daily_calorie_needs:.0f}kcal
    各食事のカロリー目安:
    朝食: {daily_calorie_needs * 0.3:.0f}kcal
    昼食: {daily_calorie_needs * 0.3:.0f}kcal
    夕食: {daily_calorie_needs * 0.4:.0f}kcal
    
    以下は、その要求に基づいて選ばれた食材から作れるレシピのリストです：
    {recipes_info}
    
    これらのレシピから、ユーザーの要求に最も適した1週間分の献立（{meal_types_str}）を作成してください。
    各食事について、条件をもとに1日ごとにステップバイステップでレシピを選定し、その理由、材料、推定カロリー、推定コスト、URLを記載してください。
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
    推定カロリー: [AIによる推定カロリー]kcal
    推定コスト: [AIによる推定コスト]円
    URL: [レシピのURL]
    """

    prompt += """
    ...

    ## 1週間分の材料まとめ:
    
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
    - 各レシピのカロリーとコストを推定し、記載してください。

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
            parsed_plan[current_date][current_meal] = {"recipe": recipe, "reason": "", "materials": "", "calories": "", "cost": "", "url": ""}
        elif line.startswith("理由:"):
            if current_date and current_meal:
                parsed_plan[current_date][current_meal]["reason"] = line.split(":", 1)[1].strip()
        elif line.startswith("材料:"):
            if current_date and current_meal:
                parsed_plan[current_date][current_meal]["materials"] = line.split(":", 1)[1].strip()
        elif line.startswith("推定カロリー:"):
            if current_date and current_meal:
                parsed_plan[current_date][current_meal]["calories"] = line.split(":", 1)[1].strip()
        elif line.startswith("推定コスト:"):
            if current_date and current_meal:
                parsed_plan[current_date][current_meal]["cost"] = line.split(":", 1)[1].strip()
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
    total_calories = 0
    total_cost = 0
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
            daily_calories = 0
            daily_cost = 0
            for meal_type, meal_info in meals.items():
                icon = get_food_icon(meal_type)
                st.markdown(f"<i class='fas {icon}'></i> **{meal_type}**", unsafe_allow_html=True)
                st.write(meal_info['recipe'])
                st.write(f"カロリー: {meal_info['calories']}")
                st.write(f"コスト: {meal_info['cost']}")
                if st.button(f"{date} {meal_type}の詳細を見る", key=f"{date}_{meal_type}"):
                    st.session_state.current_page = f"{date}_{meal_type}"
                daily_calories += extract_number(meal_info['calories'])
                daily_cost += extract_number(meal_info['cost'])
            st.write(f"1日合計: {daily_calories}kcal, {daily_cost}円")
            total_calories += daily_calories
            total_cost += daily_cost
    
    st.subheader("1週間の合計")
    st.write(f"総カロリー: {total_calories}kcal")
    st.write(f"総コスト: {total_cost}円")

def display_meal_details(date, meal_type, meal_info):
    st.subheader(f"{date} - {meal_type}")
    st.write(f"**レシピ:** {meal_info['recipe']}")
    st.write(f"**理由:** {meal_info['reason']}")
    st.write(f"**材料:** {meal_info['materials']}")
    st.write(f"**カロリー:** {meal_info['calories']}")
    st.write(f"**コスト:** {meal_info['cost']}")
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
                <p><strong>カロリー:</strong> {details['calories']}</p>
                <p><strong>コスト:</strong> {details['cost']}</p>
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

    st.subheader("主食の比重")
    rice_ratio = st.slider("ごはんもの", 0, 100, 50, 10)
    bread_ratio = st.slider("パン", 0, 100, 25, 10)
    noodle_ratio = st.slider("麺類", 0, 100, 25, 10)

    user_attributes = get_user_attributes()

    if "meal_plan" not in st.session_state:
        st.session_state.meal_plan = None
        st.session_state.materials_summary = None
        st.session_state.current_page = "calendar"
        st.session_state.debug_info = {}

    if st.button("献立を作成", key="create_plan"):
        if user_request and meal_types:
            with st.spinner("献立を作成中...しばらくお待ちください（1-2分程度かかります）"):
                progress_bar = st.progress(0)
                
                progress_bar.progress(25)
                category_ids = get_category_ids(user_request, categories, start_date, rice_ratio, bread_ratio, noodle_ratio)
                st.session_state.debug_info['category_ids'] = category_ids
                
                progress_bar.progress(50)
                recipes = get_recipes(category_ids)
                st.session_state.debug_info['recipes'] = recipes
                
                if not recipes:
                    st.warning("レシピを取得できませんでした。別の条件で再度お試しください。")
                    return

                progress_bar.progress(75)
                meal_plan_text = select_recipes_with_estimation(recipes, user_request, start_date, meal_types, rice_ratio, bread_ratio, noodle_ratio, user_attributes)
                st.session_state.debug_info['meal_plan_text'] = meal_plan_text
                
                st.session_state.meal_plan, st.session_state.materials_summary = parse_meal_plan(meal_plan_text, meal_types)
                st.session_state.debug_info['parsed_meal_plan'] = st.session_state.meal_plan
                st.session_state.debug_info['materials_summary'] = st.session_state.materials_summary
                
                progress_bar.progress(100)
                
                st.success("献立が完成しました！")
                st.session_state.current_page = "calendar"
        else:
            st.warning("要望を入力し、少なくとも1つの食事タイプを選択してください。")

    if st.session_state.meal_plan:
        st.subheader("献立の保存")
        default_save_dir = r"C:\Users\b1019\recipe_service\保存"
        save_dir = st.text_input("保存先ディレクトリを入力してください:", value=default_save_dir)
        if st.button("献立を保存"):
            try:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                
                json_path = os.path.join(save_dir, f"meal_plan_{timestamp}.json")
                save_meal_plan(st.session_state.meal_plan, st.session_state.materials_summary, json_path)
                st.success(f"献立をJSONファイルとして {json_path} に保存しました。")

                html_path = os.path.join(save_dir, f"meal_plan_{timestamp}.html")
                generate_html(st.session_state.meal_plan, st.session_state.materials_summary, html_path)
                st.success(f"HTMLファイルを {html_path} に生成しました。")
                
                if st.button("HTMLを開く", key="open_html"):
                    webbrowser.open(f"file://{os.path.abspath(html_path)}")
            except Exception as e:
                st.error(f"献立の保存中にエラーが発生しました: {str(e)}")
                st.error("ディレクトリの権限や空き容量を確認してください。")

    st.subheader("保存した献立を読み込む")
    uploaded_file = st.file_uploader("JSONファイルをアップロードしてください", type=["json"])
    
    if uploaded_file is not None:
        try:
            st.session_state.meal_plan, st.session_state.materials_summary = load_meal_plan(uploaded_file)
            st.success("献立を読み込みました。")
            st.session_state.current_page = "calendar"
        except Exception as e:
            st.error(f"献立の読み込みに失敗しました: {str(e)}")

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

    st.sidebar.title("使い方")
    st.sidebar.write("""
    1. 要望を入力欄に記入してください。
    2. 開始日を選択してください。
    3. 食事の種類を選択してください。
    4. 主食の比重を調整してください。
    5. ユーザー情報を入力してください。
    6. '献立を作成'ボタンをクリックします。
    7. 生成された献立を保存する場合は、保存先を指定して'献立を保存'ボタンをクリックしてください。
    8. カレンダー形式で献立が表示されます。
    9. 各日付の詳細を見るには、対応するボタンをクリックしてください。
    10. 保存した献立を読み込むには、JSONファイルをアップロードしてください。
    """)

if __name__ == "__main__":
    main()