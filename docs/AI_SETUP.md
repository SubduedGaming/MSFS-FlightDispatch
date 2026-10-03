# Setting up the AI dispatcher (LM Studio)

SkyDispatch talks to a language model through LM Studio's local, OpenAI-compatible server. Nothing leaves your
computer unless you point it at a remote server.

1. Install [LM Studio](https://lmstudio.ai) and download an **instruct** model. Models with tool-calling support
   give the best results (Qwen2.5-Instruct 7B or larger, Llama-3.1-Instruct 8B, Mistral-Nemo-Instruct, ...).
2. Load the model, open the **Developer** tab and click **Start Server**. The default address is
   `http://localhost:1234/v1`.
3. In SkyDispatch open **Settings > AI Dispatcher**, press **Test connection**. Pick a model from the list or leave it on
   "Whichever model is loaded".

## Using LM Studio on another computer
Enable "Serve on local network" in LM Studio, then set the server URL to `http://<that-pc>:1234/v1`.

## Tool calling
The dispatcher looks up jobs, books them, buys fuel and so on by calling functions. With **Tool calling: Automatic**
SkyDispatch tries the model's native tool calling and, if the server rejects it, switches to a plain-text protocol that
works with any model. You can force either mode in settings.

## Tips
- Slow replies? Use a smaller model or lower **Max reply length**.
- Reasoning models that print `<think>` blocks are supported; the thinking is stripped from replies.
- If the server is offline the app keeps working; the dispatcher falls back to scripted lines for briefings,
  debriefs and radio calls.
