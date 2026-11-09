using System;
using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace NabyScape;

// Official OpenRouter OAuth PKCE flow for third-party apps (openrouter.ai/docs/use-cases/oauth-pkce).
// The browser returns a one-time code to a loopback listener; the code is exchanged for a user-owned key.
internal static class AssistantOpenRouterLogin
{
    internal const string BaseUrl = "https://openrouter.ai/api/v1/";
    internal const string DefaultModel = "openrouter/auto";
    private const string Callback = "http://localhost:3000/naby-openrouter/";

    internal static string Verifier()
    {
        var bytes = RandomNumberGenerator.GetBytes(32);
        return Base64Url(bytes);
    }
    internal static string Challenge(string verifier) => Base64Url(SHA256.HashData(Encoding.ASCII.GetBytes(verifier)));
    private static string Base64Url(byte[] bytes) => Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    internal static string AuthUrl(string challenge) =>
        "https://openrouter.ai/auth?callback_url=" + Uri.EscapeDataString(Callback) + "&code_challenge=" + challenge + "&code_challenge_method=S256";

    internal static void OpenBrowser(string url) => Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });

    /// <summary>Opens the browser, waits for the user to approve and returns the new API key.</summary>
    internal static async Task<string> SignIn(CancellationToken cancel)
    {
        string verifier = Verifier();
        using var listener = new HttpListener();
        listener.Prefixes.Add(Callback);
        try { listener.Start(); }
        catch (HttpListenerException) { throw new InvalidOperationException("Port 3000 is busy. Close the app that uses it and try again."); }
        OpenBrowser(AuthUrl(Challenge(verifier)));
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancel);
        timeout.CancelAfter(TimeSpan.FromMinutes(5));
        string? code = null;
        using (timeout.Token.Register(() => { try { listener.Stop(); } catch { } }))
        {
            while (code == null)
            {
                HttpListenerContext ctx;
                try { ctx = await listener.GetContextAsync(); }
                catch (Exception) when (timeout.IsCancellationRequested) { throw new OperationCanceledException("Sign-in timed out or was cancelled."); }
                code = ctx.Request.QueryString["code"];
                string page = code != null
                    ? "<h2>NabyScape: вход выполнен</h2><p>Можно закрыть эту вкладку и вернуться в NabyScape.</p>"
                    : "<h2>NabyScape</h2><p>Код входа не получен. Попробуйте ещё раз.</p>";
                var body = Encoding.UTF8.GetBytes("<!doctype html><meta charset=utf-8><body style=\"font-family:Segoe UI;background:#16181d;color:#eee;padding:40px\">" + page);
                ctx.Response.ContentType = "text/html; charset=utf-8";
                ctx.Response.ContentLength64 = body.Length;
                await ctx.Response.OutputStream.WriteAsync(body, cancel);
                ctx.Response.Close();
            }
        }
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
        var json = JsonSerializer.Serialize(new { code, code_verifier = verifier, code_challenge_method = "S256" });
        using var response = await http.PostAsync("https://openrouter.ai/api/v1/auth/keys", new StringContent(json, Encoding.UTF8, "application/json"), cancel);
        var text = await response.Content.ReadAsStringAsync(cancel);
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException("OpenRouter did not issue a key (status " + (int)response.StatusCode + ").");
        using var doc = JsonDocument.Parse(text);
        if (!doc.RootElement.TryGetProperty("key", out var key) || key.GetString() is not { Length: > 0 } value)
            throw new InvalidOperationException("OpenRouter did not issue a key.");
        return value;
    }
}
