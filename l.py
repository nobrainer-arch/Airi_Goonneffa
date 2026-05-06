import requests
import json
import config


def test_klipy_api(api_key):
    """Test Klipy API connection with a simple trending request"""
    base_url = "https://api.klipy.com"
    endpoint = f"{base_url}/api/v1/{api_key}/gifs/trending"
    
    # Optional parameters for better results
    params = {
        "page": 1,
        "per_page": 10,
        "locale": "en"
    }
    
    print(f"\n🔄 Sending GET request to: {endpoint}")
    print(f"📡 Params: {json.dumps(params, indent=2)}")
    
    try:
        response = requests.get(endpoint, params=params)
        print(f"\n📊 Response Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get("result"):
                gifs = data.get("data", {}).get("data", [])
                print(f"✅ API is working! Found {len(gifs)} trending GIFs")
                if gifs:
                    print(f"\n📽️ First GIF preview: {gifs[0].get('media_formats', {}).get('gif', {}).get('url', 'N/A')[:100]}...")
                return True
            else:
                print(f"❌ API returned error: {data}")
                return False
        else:
            print(f"❌ HTTP error: {response.status_code}")
            print(f"Response: {response.text[:200]}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("❌ Failed to connect to Klipy API. Check your internet connection.")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def search_klipy_gifs(api_key, query):
    """Search for specific GIFs using your query"""
    base_url = "https://api.klipy.com"
    endpoint = f"{base_url}/api/v1/{api_key}/gifs/search"
    
    # Include the search query
    params = {
        "q": query,
        "page": 1,
        "per_page": 5,
        "locale": "en"
    }
    
    print(f"\n🔍 Searching for '{query}'...")
    
    try:
        response = requests.get(endpoint, params=params)
        if response.status_code == 200:
            data = response.json()
            if data.get("result"):
                gifs = data.get("data", {}).get("data", [])
                print(f"✅ Found {len(gifs)} GIFs matching '{query}'")
                if gifs:
                    print(f"\n📽️ First result preview: {gifs[0].get('media_formats', {}).get('gif', {}).get('url', 'N/A')[:100]}...")
                return True
        else:
            print(f"❌ Search failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Search error: {e}")
        return False

if __name__ == "__main__":
    # 1️⃣ GET YOUR API KEY FROM KLIPY
    # Visit: https://partner.klipy.com/ → API Keys → Create a new app key
    # Testing keys have a limit of 100 requests per minute
    
    API_KEY = config.KLIPY_API_KEY.strip() if config.KLIPY_API_KEY else None
    
    if not API_KEY:
        print("❌ No API key provided. Exiting.")
        sys.exit(1)
    
    print("\n📡 Testing Klipy API connection...")
    
    # Test trending GIFs endpoint
    if test_klipy_api(API_KEY):
        print("\n✨ API connection successful!")
        
        while True:
            print("\n📋 What would you like to do?")
            print("1. Search for specific GIFs")
            print("2. Exit")
            
            choice = input("\nEnter your choice (1-2): ").strip()
            
            if choice == "1":
                query = input("Enter search term (e.g., 'cats', 'anime', 'memes'): ")
                if query:
                    search_klipy_gifs(API_KEY, query)
                else:
                    print("❌ No search term provided.")
            elif choice == "2":
                print("👋 Goodbye!")
                break