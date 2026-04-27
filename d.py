
import requests

url = "https://hvaujoxdpowcvbcgoefk.supabase.co/rest/v1/users?select=*"
headers = {
    "apikey": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh2YXVqb3hkcG93Y3ZiY2dvZWZrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY4Mzk0NzMsImV4cCI6MjA5MjQxNTQ3M30.TXL8M0LIXUTiOc_-GeEIcTPPpVUPLwon2qCDzuMyApg",
    "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh2YXVqb3hkcG93Y3ZiY2dvZWZrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY4Mzk0NzMsImV4cCI6MjA5MjQxNTQ3M30.TXL8M0LIXUTiOc_-GeEIcTPPpVUPLwon2qCDzuMyApg"
}
response = requests.get(url, headers=headers)
print(response.json())  