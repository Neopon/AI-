import os
import requests
import time
import sys
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()

# 環境変数から楽天アプリケーションIDを取得
RAKUTEN_APP_ID = os.getenv("RAKUTEN_APP_ID")

# カテゴリIDをここで指定（カンマ区切りで複数指定可能）
CATEGORY_IDS = "10-277,10-277-519,11-70,11-70-839,12-105,12-105-75,12-107-316,19-675-1566,19-463,30-305,31-333,31-335-1267,35-468,35-471,35-472,41-534,42-554,42-559,46-596,46-596-1830"

def get_recipe(category_id):
    """
    楽天レシピAPIから指定されたカテゴリIDのレシピを取得する関数
    """
    url = "https://app.rakuten.co.jp/services/api/Recipe/CategoryRanking/20170426"
    params = {
        "applicationId": RAKUTEN_APP_ID,
        "categoryId": category_id.strip(),
        "format": "json"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get('result', [])
    except requests.exceptions.RequestException as e:
        print(f"カテゴリID {category_id} のAPIリクエストに失敗しました。エラー: {str(e)}", file=sys.stderr)
        return []

def get_recipes(category_ids):
    """
    複数のカテゴリIDに対してレシピを取得する関数
    """
    all_recipes = []
    for category_id in category_ids.split(','):
        print(f"カテゴリID {category_id} のレシピを取得中...", file=sys.stderr)
        recipes = get_recipe(category_id)
        all_recipes.extend(recipes)
        print(f"カテゴリID {category_id} から {len(recipes)} 件のレシピを取得しました。", file=sys.stderr)
        time.sleep(1)  # APIの利用制限を考慮して1秒待機
    return all_recipes

def display_recipes(recipes):
    """
    レシピをターミナルに表示する関数
    """
    print(f"\n合計 {len(recipes)} 件のレシピが見つかりました。\n")
    for i, recipe in enumerate(recipes, 1):
        print(f"レシピ {i}:")
        print(f"タイトル: {recipe['recipeTitle']}")
        print(f"URL: {recipe['recipeUrl']}")
        print("材料:")
        for material in recipe['recipeMaterial']:
            print(f"- {material}")
        print("-" * 50)

def main():
    if not RAKUTEN_APP_ID:
        print("エラー: RAKUTEN_APP_IDが設定されていません。.envファイルを確認してください。", file=sys.stderr)
        sys.exit(1)

    print("レシピを検索中...", file=sys.stderr)
    recipes = get_recipes(CATEGORY_IDS)
    
    if recipes:
        display_recipes(recipes)
    else:
        print("レシピが見つかりませんでした。", file=sys.stderr)

if __name__ == "__main__":
    main()