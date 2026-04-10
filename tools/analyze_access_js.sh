#!/bin/bash
# Analyze access.clarivate.com Angular app to find IP auth flow
# Run on jumper: bash /opt/bookstore-scraper/tools/analyze_access_js.sh

TMPDIR="/tmp/access-js"
mkdir -p "$TMPDIR"

echo "=== 1. Downloading JS files ==="
curl -s "https://access.clarivate.com/public/main.bd18dd664c8a192d.js" -o "$TMPDIR/main.js"
curl -s "https://access.clarivate.com/public/659.2c3f1d2266cb8055.js" -o "$TMPDIR/659.js"
curl -s "https://access.clarivate.com/public/664.1f061d59b157202e.js" -o "$TMPDIR/664.js"
curl -s "https://access.clarivate.com/public/976.47c6751bb0739ea1.js" -o "$TMPDIR/976.js"
curl -s "https://access.clarivate.com/public/384.6043af15c3b7b686.js" -o "$TMPDIR/384.js"
curl -s "https://access.clarivate.com/public/147.c858b2c002b7c3f9.js" -o "$TMPDIR/147.js"
curl -s "https://access.clarivate.com/public/0.87f5e28162e08025.js" -o "$TMPDIR/0.js"
curl -s "https://access.clarivate.com/public/817.c79c77ae1565a5a3.js" -o "$TMPDIR/817.js"
curl -s "https://access.clarivate.com/public/560.307eea494db12d50.js" -o "$TMPDIR/560.js"
echo "Downloaded $(ls $TMPDIR/*.js | wc -l) JS files"

echo ""
echo "=== 2. All API endpoints ==="
grep -ohP '"/api/[^"]*"' $TMPDIR/*.js | sort -u
grep -ohP '"/app/api/[^"]*"' $TMPDIR/*.js | sort -u

echo ""
echo "=== 3. IP authorize related code ==="
for f in $TMPDIR/*.js; do
    matches=$(grep -c 'ip.authorize\|ip/authorize\|IP_AUTHORIZE\|ipAuthorize\|IpAuthorize' "$f" 2>/dev/null)
    if [ "$matches" -gt 0 ]; then
        echo "--- $(basename $f): $matches matches ---"
        # Use tr to split minified code into readable chunks at semicolons
        tr ';' '\n' < "$f" | grep -i 'ip.authorize\|IP_AUTHORIZE\|ipAuthorize' | head -20
    fi
done

echo ""
echo "=== 4. detectSession logic ==="
for f in $TMPDIR/*.js; do
    matches=$(grep -c 'detectSession' "$f" 2>/dev/null)
    if [ "$matches" -gt 0 ]; then
        echo "--- $(basename $f): $matches matches ---"
        tr ';' '\n' < "$f" | grep -i 'detectSession' | head -20
    fi
done

echo ""
echo "=== 5. API key / auth header ==="
for f in $TMPDIR/*.js; do
    # Search for common API key header patterns
    tr ';' '\n' < "$f" | grep -iP '(api.?key|x-api|x-1p|apikey|"key"|authorization.*bearer)' | head -10
done

echo ""
echo "=== 6. HTTP interceptor / headers ==="
for f in $TMPDIR/*.js; do
    tr ';' '\n' < "$f" | grep -iP '(interceptor|setHeader|httpHeaders|append.*header)' | head -10
done

echo ""
echo "=== 7. Session detection flow ==="
for f in $TMPDIR/*.js; do
    matches=$(grep -c 'session\|Session' "$f" 2>/dev/null)
    if [ "$matches" -gt 5 ]; then
        echo "--- $(basename $f): $matches matches ---"
        tr ';' '\n' < "$f" | grep -iP '(detectSession|sessionDetect|checkSession|getSession|session.*detect|auto.*login|auto.*auth|ip.*auth)' | head -20
    fi
done

echo ""
echo "=== 8. Environment / config endpoints ==="
for f in $TMPDIR/*.js; do
    tr ';' '\n' < "$f" | grep -iP '(environment|config|endpoint|BASE_URL|API_URL)' | grep -iP '(http|api|url)' | head -10
done

echo ""
echo "=== 9. Test IP authorize API with various methods ==="
echo "--- GET ---"
curl -s -w "\nHTTP %{http_code}\n" "https://access.clarivate.com/api/ip/authorize?app=jcr"

echo "--- POST with empty body ---"
curl -s -w "\nHTTP %{http_code}\n" -X POST "https://access.clarivate.com/api/ip/authorize?app=jcr" -H "Content-Type: application/json" -d '{}'

echo "--- POST with app body ---"
curl -s -w "\nHTTP %{http_code}\n" -X POST "https://access.clarivate.com/api/ip/authorize?app=jcr" -H "Content-Type: application/json" -d '{"app":"jcr"}'

echo "--- GET /app/api/user/ip/auth ---"
curl -s -w "\nHTTP %{http_code}\n" "https://access.clarivate.com/app/api/user/ip/auth?app=jcr"

echo "--- POST /app/api/user/ip/auth ---"
curl -s -w "\nHTTP %{http_code}\n" -X POST "https://access.clarivate.com/app/api/user/ip/auth" -H "Content-Type: application/json" -d '{"app":"jcr"}'

echo "--- GET with Origin header ---"
curl -s -w "\nHTTP %{http_code}\n" "https://access.clarivate.com/api/ip/authorize?app=jcr" -H "Origin: https://access.clarivate.com"

echo "--- GET with Referer ---"
curl -s -w "\nHTTP %{http_code}\n" "https://access.clarivate.com/api/ip/authorize?app=jcr" -H "Referer: https://access.clarivate.com/login?app=jcr&detectSession=true"

echo ""
echo "=== 10. Cookie-based session test ==="
echo "--- Full login chain with cookie jar ---"
COOKIEJAR="$TMPDIR/cookies.txt"
rm -f "$COOKIEJAR"
# Step 1: Visit login page to get initial cookies
curl -s -L -b "$COOKIEJAR" -c "$COOKIEJAR" "https://access.clarivate.com/login?app=jcr&detectSession=true" -o /dev/null
echo "Cookies after login page:"
cat "$COOKIEJAR" 2>/dev/null | grep -v '^#' | grep -v '^$'
# Step 2: Try IP authorize with those cookies
echo "--- IP authorize with cookies ---"
curl -s -w "\nHTTP %{http_code}\n" -b "$COOKIEJAR" -c "$COOKIEJAR" "https://access.clarivate.com/api/ip/authorize?app=jcr"

echo ""
echo "=== Done ==="
