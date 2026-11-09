using System;
using System.Linq;
using System.Threading;
using System.Windows;
using System.Windows.Controls;

namespace NabyScape;

// Quick start: sign in to OpenRouter (official OAuth PKCE), get a free Gemini key, or use a local model.
internal sealed partial class AssistantProviderSettings
{
    private Button? openRouterButton;
    private CancellationTokenSource? openRouterCancel;

    private UIElement QuickStart()
    {
        var box = new StackPanel { Margin = new(0, 4, 0, 12) };
        box.Children.Add(UI.Text("Quick start · no API setup needed", 13, UI.Muted));
        var row = new WrapPanel { Margin = new(0, 6, 0, 4) };
        openRouterButton = AssistantWindow.Pill("Sign in with OpenRouter", () => _ = OpenRouterSignIn(), true);
        row.Children.Add(openRouterButton);
        row.Children.Add(AssistantWindow.Pill("Get a free Gemini key", GeminiKeyHelper));
        row.Children.Add(AssistantWindow.Pill("Local model (Ollama)", OllamaHelper));
        box.Children.Add(row);
        box.Children.Add(UI.Text("OpenRouter: sign in in your browser and the key is saved automatically. Claude, GPT, Gemini and free models are available; usage is billed to your OpenRouter balance. Gemini: Google AI Studio has a free tier. Ollama runs models on this PC for free.", 11, UI.Muted));
        return box;
    }

    private AssistantProvider? EnsurePreset(string preset)
    {
        var template = AssistantProviderCatalog.Create(preset);
        var existing = ai.Options.Providers.FirstOrDefault(p => p.BaseUrl.TrimEnd('/') == template.BaseUrl.TrimEnd('/'));
        if (existing != null) return existing;
        if (ai.Options.Providers.Count >= 12) { Feedback("Keep up to 12 providers. Remove one before adding another.", true); return null; }
        if (preset == "Google Gemini") { template.RealtimeProtocol = "GeminiLive"; template.RealtimeUrl = AssistantVoiceSession.DefaultUrl(template.RealtimeProtocol); template.Voice = "Kore"; }
        if (preset == "OpenRouter") template.Model = AssistantOpenRouterLogin.DefaultModel;
        ai.Options.Providers.Add(template);
        if (ai.Options.SelectedProvider.Length == 0) ai.Options.SelectedProvider = template.Id;
        SaveConfiguration();
        return template;
    }

    private async System.Threading.Tasks.Task OpenRouterSignIn()
    {
        if (openRouterCancel != null) { openRouterCancel.Cancel(); return; }
        if (!await ConfirmProviderChanges()) return;
        openRouterCancel = new CancellationTokenSource();
        if (openRouterButton != null) openRouterButton.Content = Localization.Text("Cancel sign-in");
        Feedback("Waiting for OpenRouter sign-in in your browser…");
        try
        {
            string apiKey = await AssistantOpenRouterLogin.SignIn(openRouterCancel.Token);
            var provider = EnsurePreset("OpenRouter");
            if (provider == null) return;
            var candidate = System.Text.Json.JsonSerializer.Deserialize<AssistantProvider>(System.Text.Json.JsonSerializer.Serialize(provider))!;
            if (candidate.Model.Length == 0) candidate.Model = AssistantOpenRouterLogin.DefaultModel;
            ai.SaveProvider(provider.Id, candidate, apiKey);
            if (!ai.Options.Enabled) { ai.Options.Enabled = true; ai.SaveOptions(); }
            Reload(ai.Options.SelectedProvider); RefreshEnabled();
            Feedback("Signed in with OpenRouter · key saved in Windows. Model: automatic choice; you can pick another in Edit connection.");
        }
        catch (OperationCanceledException) { Feedback("OpenRouter sign-in cancelled.", true); }
        catch (Exception ex) { Feedback(Localization.Text("Sign-in failed · ") + Localization.Text(ex.Message), true); }
        finally
        {
            openRouterCancel?.Dispose(); openRouterCancel = null;
            if (openRouterButton != null) openRouterButton.Content = Localization.Text("Sign in with OpenRouter");
        }
    }

    private void GeminiKeyHelper()
    {
        try { AssistantOpenRouterLogin.OpenBrowser("https://aistudio.google.com/apikey"); } catch { }
        var provider = EnsurePreset("Google Gemini");
        if (provider == null) return;
        Reload(provider.Id);
        connectionEditor.IsExpanded = true; key.Focus();
        Feedback("In Google AI Studio choose Create API key, copy it, paste it into the API key field below and choose Save provider.");
    }

    private void OllamaHelper()
    {
        try { AssistantOpenRouterLogin.OpenBrowser("https://ollama.com/download"); } catch { }
        var provider = EnsurePreset("Ollama");
        if (provider == null) return;
        Reload(provider.Id);
        connectionEditor.IsExpanded = true;
        Feedback("Install Ollama, run «ollama pull llama3.2» in a terminal, then choose a model below and Save provider. No key is needed.");
    }
}
