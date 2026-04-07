

import requests

def test_waifu_nsfw(category="waifu"):
    """Test NSFW endpoint - categories: waifu, neko, trap, blowjob"""
    url = f"https://api.waifu.pics/nsfw/{category}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            img_url = resp.json().get('url')
            print(f"✅ NSFW {category}: {img_url}")
            print(f"🎞️ Is GIF: {img_url.endswith('.gif')}")
            return img_url
        else:
            print(f"❌ Error {resp.status_code}")
            return None
    except Exception as e:
        print(f"❌ Failed: {e}")
        return None

# Test it
test_waifu_nsfw("waifu")

import requests

def test_danbooru(tag="1girl", limit=1):
    """Search Danbooru by tag - use SFW tags like '1girl', 'solo'"""
    url = f"https://danbooru.donmai.us/posts.json?tags={tag}&limit={limit}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                img_url = data[0].get('file_url')
                print(f"✅ Tag '{tag}': {img_url}")
                return img_url
            else:
                print("❌ No results")
                return None
        else:
            print(f"❌ Error {resp.status_code}")
            return None
    except Exception as e:
        print(f"❌ Failed: {e}")
        return None

# Test with SFW tag
test_danbooru("1girl")

import requests

def test_nekoslife_nsfw(category="random_hentai_gif"):
    """Categories: random_hentai_gif, pussy, blowjob, nsfw_neko_gif"""
    url = f"https://nekos.life/api/v2/img/{category}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            img_url = resp.json().get('url')
            print(f"✅ NSFW: {img_url}")
            return img_url
        else:
            print(f"❌ Error {resp.status_code}")
            return None
    except Exception as e:
        print(f"❌ Failed: {e}")
        return None

test_nekoslife_nsfw("random_hentai_gif")

import requests

def test_nekobot_nsfw(category="hboobs"):
    """Categories: hboobs, hmidriff, hthigh, anal, hanal, hkitsune"""
    url = f"https://nekobot.xyz/api/image?type={category}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            img_url = resp.json().get('message')
            print(f"✅ NSFW {category}: {img_url}")
            return img_url
        else:
            print(f"❌ Error {resp.status_code}")
            return None
    except Exception as e:
        print(f"❌ Failed: {e}")
        return None

test_nekobot_nsfw("hboobs")