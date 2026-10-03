package dev.wikiagent.mobile;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.res.ColorStateList;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.window.OnBackInvokedDispatcher;
import android.webkit.CookieManager;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.TextView;
import android.widget.Toast;

import com.google.mlkit.vision.barcode.common.Barcode;
import com.google.mlkit.vision.codescanner.GmsBarcodeScanner;
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions;
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning;

import java.net.URI;
import java.util.Optional;

public final class MainActivity extends Activity {
    private static final String PREFS = "mobile";
    private static final String ORIGIN = "origin";
    private static final int PANEL = Color.rgb(28, 31, 37);
    private static final int GROUND = Color.rgb(20, 22, 26);
    private static final int INK = Color.rgb(236, 234, 229);
    private static final int MUTED = Color.rgb(168, 174, 182);
    private static final int PRIMARY = Color.rgb(127, 169, 201);

    private SharedPreferences preferences;
    private FrameLayout stage;
    private LinearLayout intro;
    private WebView webView;
    private Button connectionMenu;
    private URI origin;
    private ScreenMode screenMode;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (Build.VERSION.SDK_INT >= 30) getWindow().setDecorFitsSystemWindows(false);
        preferences = getSharedPreferences(PREFS, MODE_PRIVATE);
        screenMode = ScreenMode.stored(preferences.getString("screen-mode", "portrait"));
        setRequestedOrientation(screenMode.orientation);
        setContentView(screen());
        Optional<URI> saved = PairingLink.savedOrigin(preferences.getString(ORIGIN, ""));
        if (saved.isPresent()) open(saved.get());
        else showIntro();
        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::back);
        }
    }

    private View screen() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(GROUND);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                Insets safe = insets.getInsets(WindowInsets.Type.systemBars()
                        | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(safe.left, safe.top, safe.right, safe.bottom);
                return WindowInsets.CONSUMED;
            } else {
                view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                        insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            }
            return insets.consumeSystemWindowInsets();
        });

        stage = new FrameLayout(this);
        webView = webView();
        stage.addView(webView, match());
        intro = intro();
        stage.addView(intro, match());
        root.addView(stage, match());
        connectionMenu = button("⋮", this::connectionMenu);
        connectionMenu.setContentDescription("앱 메뉴 · 화면 모드 및 연결");
        connectionMenu.setTextColor(INK);
        connectionMenu.setBackgroundTintList(ColorStateList.valueOf(PANEL));
        FrameLayout.LayoutParams menuLayout = new FrameLayout.LayoutParams(dp(44), dp(44), Gravity.TOP | Gravity.START);
        menuLayout.setMarginStart(dp(4));
        menuLayout.topMargin = dp(2);
        root.addView(connectionMenu, menuLayout);
        return root;
    }

    private void connectionMenu(View anchor) {
        PopupMenu menu = new PopupMenu(this, anchor);
        menu.getMenu().add(0, 1, 0, "연결 QR 스캔");
        menu.getMenu().add(0, 2, 1, "클립보드 링크 연결");
        menu.getMenu().add(0, 3, 2, "다시 불러오기");
        menu.getMenu().add(1, 4, 3, "세로 모드").setCheckable(true).setChecked(screenMode == ScreenMode.PORTRAIT);
        menu.getMenu().add(1, 5, 4, "가로 모드").setCheckable(true).setChecked(screenMode == ScreenMode.LANDSCAPE);
        menu.getMenu().setGroupCheckable(1, true, true);
        menu.setOnMenuItemClickListener(item -> {
            if (item.getItemId() == 1) scan();
            else if (item.getItemId() == 2) paste();
            else if (item.getItemId() == 3) webView.reload();
            else selectMode(item.getItemId() == 5 ? ScreenMode.LANDSCAPE : ScreenMode.PORTRAIT);
            return true;
        });
        menu.show();
    }

    private void selectMode(ScreenMode mode) {
        screenMode = mode;
        preferences.edit().putString("screen-mode", mode.layout).apply();
        // Explicit orientation works even when the phone's auto-rotate is off.
        // Never read sensors or write the system rotation setting.
        setRequestedOrientation(mode.orientation);
        publishScreenMode();
    }

    private void publishScreenMode() {
        String url = webView.getUrl();
        if (origin == null || url == null || !PairingLink.sameOrigin(origin, url)) return;
        // One-way public presentation hint, not a JavaScript-to-native bridge.
        webView.evaluateJavascript("document.documentElement.dataset.nativeShell='android';"
                + "document.documentElement.dataset.nativeMode='" + screenMode.layout + "';"
                + "window.dispatchEvent(new CustomEvent('mobile-screen-mode',{detail:'"
                + screenMode.layout + "'}))", null);
    }

    @Override
    public void onConfigurationChanged(Configuration configuration) {
        super.onConfigurationChanged(configuration);
        // Retain the WebView, stream, scroll position and unsent drafts.
        // The chosen mode owns the layout; configuration.orientation does not.
        webView.invalidate();
        publishScreenMode();
    }

    private LinearLayout intro() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setGravity(Gravity.CENTER);
        card.setPadding(dp(24), dp(24), dp(24), dp(24));
        card.setBackgroundColor(GROUND);

        TextView heading = text("휴대폰 연결", 22, INK);
        heading.setGravity(Gravity.CENTER);
        card.addView(heading, wrap(dp(8)));
        TextView detail = text("PC의 설정 → 휴대폰에서 연결 링크를 만든 뒤 QR 코드를 스캔하세요.", 16, MUTED);
        detail.setGravity(Gravity.CENTER);
        detail.setLineSpacing(0, 1.25f);
        card.addView(detail, wrap(dp(20)));
        card.addView(button("연결 QR 스캔", ignored -> scan()), new LinearLayout.LayoutParams(dp(220), dp(48)));
        Button paste = button("클립보드 링크 연결", ignored -> paste());
        LinearLayout.LayoutParams pasteLayout = new LinearLayout.LayoutParams(dp(220), dp(48));
        pasteLayout.topMargin = dp(12);
        card.addView(paste, pasteLayout);
        return card;
    }

    private WebView webView() {
        WebView view = new WebView(this);
        view.setBackgroundColor(GROUND);
        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        settings.setGeolocationEnabled(false);
        settings.setSupportMultipleWindows(true);
        settings.setSafeBrowsingEnabled(true);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(view, false);
        WebView.setWebContentsDebuggingEnabled((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0);
        view.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView page, String url) {
                if (origin != null && PairingLink.sameOrigin(origin, url)) {
                    publishScreenMode();
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView ignored, WebResourceRequest request) {
                if (origin != null && PairingLink.sameOrigin(origin, request.getUrl().toString())) return false;
                if (!request.isForMainFrame()) return true;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, request.getUrl()));
                } catch (RuntimeException error) {
                    toast("외부 링크를 열 수 없습니다.");
                }
                return true;
            }

            @Override
            public void onReceivedSslError(WebView ignored, SslErrorHandler handler, SslError error) {
                handler.cancel();
                toast("안전한 HTTPS 연결을 확인할 수 없습니다.");
            }
        });
        return view;
    }

    private void scan() {
        GmsBarcodeScannerOptions options = new GmsBarcodeScannerOptions.Builder()
                .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
                .enableAutoZoom()
                .build();
        GmsBarcodeScanner scanner = GmsBarcodeScanning.getClient(this, options);
        scanner.startScan()
                .addOnSuccessListener(barcode -> connect(barcode.getRawValue()))
                .addOnFailureListener(error -> toast("QR 스캐너를 열 수 없습니다. 클립보드 연결을 사용하세요."));
    }

    private void paste() {
        ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        ClipData clip = clipboard.getPrimaryClip();
        CharSequence value = clip == null || clip.getItemCount() == 0 ? null : clip.getItemAt(0).coerceToText(this);
        connect(value == null ? null : value.toString());
    }

    private void connect(String raw) {
        Optional<URI> parsed = PairingLink.parse(raw);
        if (!parsed.isPresent()) {
            toast("wiki-agent가 만든 HTTPS 연결 QR 또는 링크가 아닙니다.");
            return;
        }
        URI pairing = parsed.get();
        URI nextOrigin = PairingLink.origin(pairing);
        preferences.edit().putString(ORIGIN, nextOrigin.toString()).apply();
        CookieManager cookies = CookieManager.getInstance();
        cookies.removeAllCookies(ignored -> {
            cookies.flush();
            runOnUiThread(() -> {
                origin = nextOrigin;
                intro.setVisibility(View.GONE);
                connectionMenu.setVisibility(View.VISIBLE);
                webView.setVisibility(View.VISIBLE);
                webView.loadUrl(pairing.toString());
            });
        });
    }

    private void open(URI saved) {
        origin = saved;
        intro.setVisibility(View.GONE);
        connectionMenu.setVisibility(View.VISIBLE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl(saved.toString());
    }

    private void showIntro() {
        webView.setVisibility(View.GONE);
        connectionMenu.setVisibility(View.GONE);
        intro.setVisibility(View.VISIBLE);
    }

    private Button button(String label, View.OnClickListener listener) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextSize(14);
        button.setTextColor(GROUND);
        button.setAllCaps(false);
        button.setBackgroundTintList(ColorStateList.valueOf(PRIMARY));
        button.setOnClickListener(listener);
        return button;
    }

    private TextView text(String value, float size, int color) {
        TextView text = new TextView(this);
        text.setText(value);
        text.setTextSize(size);
        text.setTextColor(color);
        text.setGravity(Gravity.CENTER_VERTICAL);
        return text;
    }

    private LinearLayout.LayoutParams wrap(int bottomMargin) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.bottomMargin = bottomMargin;
        return params;
    }

    private FrameLayout.LayoutParams match() {
        return new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show();
    }

    @Override
    public void onBackPressed() {
        back();
    }

    private void back() {
        if (webView != null && webView.isShown() && webView.canGoBack()) webView.goBack();
        else finish();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            stage.removeView(webView);
            webView.destroy();
        }
        super.onDestroy();
    }
}
