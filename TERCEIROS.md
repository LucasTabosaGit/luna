# Componentes de terceiros

O **código** da Luna é MIT, © 2026 Lucas Tabosa (Krosten) — veja `LICENSE`. Ela usa programas, modelos e serviços de terceiros,
cada um com a própria licença. **Nenhum modelo de terceiros vem dentro deste repositório**:
todos baixam dos repositórios oficiais na primeira vez, e o uso segue a licença de cada um.

## Modelos (baixados na primeira vez)

| Modelo | Para quê | Licença |
|---|---|---|
| [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) (conversão [CTranslate2](https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2)) | transcrição | MIT |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | voz de reserva, quando a internet cai | Apache-2.0 |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) + [modelo ggml large-v3-turbo](https://huggingface.co/ggerganov/whisper.cpp) | ouvido na placa AMD/Intel (Vulkan, opcional) | MIT |
| [Silero VAD](https://github.com/snakers4/silero-vad) | detectar fala | MIT |
| [openWakeWord](https://github.com/dscripka/openWakeWord) `melspectrogram.onnx` e `embedding_model.onnx` | base do detector do nome | **CC BY-NC-SA 4.0** (uso não comercial) |

> **Atenção, uso comercial:** os modelos de base do openWakeWord são **não comerciais**.
> Se for usar a Luna num produto pago, troque o detector (ou use só a detecção pelo texto do Whisper,
> que funciona sem ele) e confira as licenças acima.

O classificador do nome em `recursos/detector/luna.pkl` foi treinado para este projeto só com
vozes sintéticas; é distribuído sob a mesma licença MIT do código.

## Serviços (com a sua conta e a sua chave)

| Serviço | Para quê | Termos |
|---|---|---|
| IA principal, à sua escolha: [DeepSeek](https://platform.deepseek.com), [Gemini](https://aistudio.google.com), [OpenAI](https://platform.openai.com), [Groq](https://console.groq.com), [OpenRouter](https://openrouter.ai), [Ollama](https://ollama.com) | entender e conversar (uma delas) | termos de cada serviço |
| [Hermes Agent](https://hermes-agent.nousresearch.com) + Claude | tarefas difíceis (opcional) | MIT (Hermes) + termos da Anthropic |
| Microsoft Edge TTS (via [edge-tts](https://github.com/rany2/edge-tts)) | as vozes (Francisca, Thalita, Antônio, JARVIS) | serviço online da Microsoft; biblioteca LGPL-3.0 |
| [Meu Hoje](https://meuhoje.com.br) | aba Hoje (opcional) | termos do Meu Hoje |

As vozes **"JARVIS"** são um estilo (voz sintética do Edge com filtro), não a voz do filme nem de nenhum ator.

## Bibliotecas Python principais

| Pacote | Licença |
|---|---|
| PyTorch | BSD-3-Clause |
| faster-whisper, CTranslate2, onnxruntime, FastAPI, sounddevice, comtypes, mss | MIT |
| kokoro, misaki, openWakeWord (código), openai | Apache-2.0 |
| NumPy, SciPy, scikit-learn, uvicorn, websockets, httpx, pypdf, psutil, soundfile | BSD |
| Pillow | MIT-CMU |
| edge-tts | LGPL-3.0 |
| phonemizer-fork (dependência do Kokoro para alguns idiomas) | GPL-3.0+ |
| eSpeak NG (via espeakng-loader) | GPL-3.0 |

As bibliotecas são instaladas pelo `pip` a partir do PyPI e **não são redistribuídas** neste repositório.
A lista completa, com versões, está em `requirements.txt`.
