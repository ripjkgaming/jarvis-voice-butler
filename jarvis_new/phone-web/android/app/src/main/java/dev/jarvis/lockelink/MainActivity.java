package dev.jarvis.lockelink;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import androidx.webkit.WebSettingsCompat;
import androidx.webkit.WebViewFeature;

import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;

/**
 * Thin shell around the LOCKE//LINK PWA.
 *
 * The page is loaded from the REAL origin (https://host/), so cookies, the CSP, WebAuthn's rpId
 * and the bridge's same-origin check all behave exactly as in Chrome. The shell only answers
 * GET requests for the bundled files (assets/www) locally, which makes the first paint
 * independent of the network. /api/* and /voice are never served locally: they go to the host.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "link";
    private static final String KEY_ORIGIN = "origin";
    private static final int REQ_MIC = 7;

    private WebView web;
    private PermissionRequest pendingMic;
    private String origin;
    private String host;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        origin = loadOrigin();
        host = Uri.parse(origin).getHost();

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#04081a"));
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#04081a"));
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        applyInsets(root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_AUTHENTICATION)) {
            WebSettingsCompat.setWebAuthenticationSupport(
                s, WebSettingsCompat.WEB_AUTHENTICATION_SUPPORT_FOR_APP);
        }
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setGeolocationEnabled(false);
        s.setSupportMultipleWindows(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setUserAgentString(s.getUserAgentString() + " LockeLinkApp/1");
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);

        web.setWebViewClient(new Client());
        web.setWebChromeClient(new Chrome());

        if (b != null) web.restoreState(b);
        else {
            String u = startUrl(getIntent());
            host = Uri.parse(origin).getHost();
            web.loadUrl(u);
        }
    }

    @Override
    protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        setIntent(i);
        String u = startUrl(i);
        // A VIEW intent (pairing link) always navigates; a plain relaunch keeps the session.
        if (Intent.ACTION_VIEW.equals(i.getAction())) {
            host = Uri.parse(origin).getHost();
            web.loadUrl(u);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    protected void onResume() {
        super.onResume();
        CookieManager.getInstance().flush();
    }

    @Override
    protected void onPause() {
        CookieManager.getInstance().flush();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        web.destroy();
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else moveTaskToBack(true);
    }

    // ------------------------------------------------------------------ origin

    private String loadOrigin() {
        SharedPreferences p = getSharedPreferences(PREFS, MODE_PRIVATE);
        String o = p.getString(KEY_ORIGIN, BuildConfig.LINK_ORIGIN);
        return validOrigin(o) ? o : BuildConfig.LINK_ORIGIN;
    }

    private static boolean validOrigin(String o) {
        if (o == null) return false;
        Uri u = Uri.parse(o);
        return "https".equals(u.getScheme()) && u.getHost() != null && u.getUserInfo() == null
                && (u.getPath() == null || u.getPath().isEmpty() || "/".equals(u.getPath()))
                && u.getQuery() == null && u.getFragment() == null;
    }

    /** Maps an incoming intent to the URL to show; may persist an origin override. */
    private String startUrl(Intent i) {
        Uri d = i == null ? null : i.getData();
        if (d != null) {
            if ("lockelink".equals(d.getScheme())) {
                String what = d.getHost();
                if ("origin".equals(what)) {
                    String u = d.getQueryParameter("u");
                    if (validOrigin(u)) {
                        origin = u.replaceAll("/+$", "");
                        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_ORIGIN, origin).apply();
                    }
                } else if ("pair".equals(what)) {
                    String c = d.getQueryParameter("code");
                    if (c != null && c.matches("[0-9A-Za-z-]{10,16}")) return origin + "/#pair=" + c;
                }
            } else if ("https".equals(d.getScheme()) && host != null && host.equalsIgnoreCase(d.getHost())) {
                String frag = d.getFragment();
                if (frag != null && frag.matches("pair=[0-9A-Za-z-]{10,16}")) return origin + "/#" + frag;
            }
        }
        return origin + "/";
    }

    // ------------------------------------------------------------------ insets

    private void applyInsets(final View root) {
        if (Build.VERSION.SDK_INT >= 30) {
            root.setOnApplyWindowInsetsListener((v, ins) -> {
                android.graphics.Insets bars = ins.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
                v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                return WindowInsets.CONSUMED;
            });
        } else {
            root.setFitsSystemWindows(true);
        }
    }

    // ------------------------------------------------------------------ web clients

    private static final Map<String, String> MIME = new HashMap<>();
    static {
        MIME.put("html", "text/html");
        MIME.put("js", "text/javascript");
        MIME.put("mjs", "text/javascript");
        MIME.put("css", "text/css");
        MIME.put("json", "application/json");
        MIME.put("webmanifest", "application/manifest+json");
        MIME.put("png", "image/png");
        MIME.put("svg", "image/svg+xml");
        MIME.put("woff2", "font/woff2");
        MIME.put("txt", "text/plain");
    }

    private WebResourceResponse local(Uri u) {
        String path = u.getPath() == null ? "/" : u.getPath();
        if (path.startsWith("/api/") || path.equals("/api") || path.startsWith("/voice")) return null;
        if (path.endsWith("/")) path = path + "index.html";
        if (path.contains("..")) return null;
        String name = "www" + path;
        try {
            InputStream in = getAssets().open(name);
            String ext = name.substring(name.lastIndexOf('.') + 1);
            String mime = MIME.get(ext);
            if (mime == null) {
                in.close();
                return null;
            }
            WebResourceResponse r = new WebResourceResponse(mime, "utf-8", in);
            Map<String, String> h = new HashMap<>();
            h.put("Cache-Control", "no-store");
            r.setResponseHeaders(h);
            return r;
        } catch (IOException e) {
            return null; // not bundled: let the network answer
        }
    }

    private boolean ours(Uri u) {
        Uri expected = Uri.parse(origin);
        int expectedPort = expected.getPort() == -1 ? 443 : expected.getPort();
        int actualPort = u.getPort() == -1 ? 443 : u.getPort();
        return "https".equals(u.getScheme()) && host != null && host.equalsIgnoreCase(u.getHost())
                && u.getUserInfo() == null && expectedPort == actualPort;
    }

    private class Client extends WebViewClient {
        @Override
        public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest req) {
            Uri u = req.getUrl();
            if (!"GET".equals(req.getMethod()) || !ours(u)) return null;
            return local(u);
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
            return !ours(req.getUrl()); // never navigate away from the host
        }
    }

    private class Chrome extends WebChromeClient {
        @Override
        public void onPermissionRequest(final PermissionRequest req) {
            runOnUiThread(() -> {
                boolean mine = ours(req.getOrigin());
                boolean audioOnly = req.getResources().length > 0;
                for (String r : req.getResources()) if (!PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(r)) audioOnly = false;
                if (!mine || !audioOnly) {
                    req.deny();
                    return;
                }
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                    req.grant(req.getResources());
                } else {
                    pendingMic = req;
                    requestPermissions(new String[] {Manifest.permission.RECORD_AUDIO}, REQ_MIC);
                }
            });
        }

        @Override
        public void onPermissionRequestCanceled(PermissionRequest req) {
            if (pendingMic == req) pendingMic = null;
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        if (code != REQ_MIC || pendingMic == null) return;
        PermissionRequest r = pendingMic;
        pendingMic = null;
        if (res.length > 0 && res[0] == PackageManager.PERMISSION_GRANTED) r.grant(r.getResources());
        else r.deny();
    }
}
