"""Correções de ambiente que o Kokoro exige no Windows.

Importar este módulo ANTES de `from kokoro import KPipeline`.

Dois defeitos reais, medidos nesta máquina:

1. espeak-ng com caminho de build embutido
   O phonemizer tenta abrir
       D:/a/espeakng-loader/espeakng-loader/espeak-ng/_dynamic/share/...
   que é o caminho da máquina de CI onde o wheel foi compilado. Em
   qualquer outro computador isso falha com "No such file or directory:
   phontab". A biblioteca e os dados existem dentro do site-packages;
   basta apontar o phonemizer para lá.

2. SSL: CERTIFICATE_VERIFY_FAILED — self-signed certificate in chain
   Acontece quando um antivírus/proxy corporativo reescreve o TLS. O
   download do .pth do Kokoro morre no meio. Usar os certificados do
   sistema Windows (truststore) resolve sem desligar a verificação —
   desligar seria trocar um problema por um buraco de segurança.
"""
from __future__ import annotations

import os
from pathlib import Path


def _corrigir_ssl() -> str:
    try:
        import truststore

        truststore.inject_into_ssl()
        return "truststore (certificados do Windows)"
    except Exception:
        pass
    try:
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
        return "certifi"
    except Exception:
        return "nenhum (download pode falhar atrás de proxy)"


def _espelhar_ascii(lib: Path, dados: Path) -> "tuple[Path, Path] | None":
    """Copia lib+dados do espeak para um caminho sem acentos.

    A DLL do espeak-ng abre seus arquivos de dados pela API ANSI do
    Windows. Num caminho como "D:\\Aplicações\\..." o "ç"/"õ" não
    sobrevivem à conversão e a abertura de `phontab` falha com "Illegal
    byte sequence" — o erro não diz que a causa é o caminho, e o phontab
    existe e é legível pelo Python, o que torna o diagnóstico enganoso.

    O espelho fica em %LOCALAPPDATA%\\espeak-ng-ascii (ou <disco>:\\.espeak-ng)
    e só é recriado quando falta algo.
    """
    import shutil

    candidatos = []
    base = os.environ.get("LOCALAPPDATA")
    if base and str(base).isascii():
        candidatos.append(Path(base) / "espeak-ng-ascii")
    candidatos.append(Path(lib.drive + "/") / ".espeak-ng")
    candidatos.append(Path.home() / ".espeak-ng")

    for destino in candidatos:
        if not str(destino).isascii():
            continue
        try:
            destino.mkdir(parents=True, exist_ok=True)
            lib_nova = destino / lib.name
            dados_novos = destino / dados.name

            if not lib_nova.exists():
                shutil.copy2(lib, lib_nova)
            if not (dados_novos / "phontab").exists():
                if dados_novos.exists():
                    shutil.rmtree(dados_novos, ignore_errors=True)
                shutil.copytree(dados, dados_novos)

            if lib_nova.exists() and (dados_novos / "phontab").exists():
                return lib_nova, dados_novos
        except Exception:
            continue
    return None


def _corrigir_espeak() -> str:
    try:
        import espeakng_loader
    except Exception:
        return "espeakng_loader ausente"

    # Caminho com acento quebra a DLL do espeak-ng ("Illegal byte
    # sequence" ao abrir phontab): ela abre arquivos pela API ANSI, e
    # "Aplicações" não sobrevive à conversão. Medido nesta máquina.
    # Por isso os dados são espelhados num caminho ASCII puro, e um
    # override por variável de ambiente tem prioridade.
    lib_env = os.environ.get("PHONEMIZER_ESPEAK_LIBRARY")
    dados_env = os.environ.get("ESPEAK_DATA_PATH")

    if lib_env and dados_env and Path(lib_env).exists() and Path(dados_env).exists():
        lib, dados = Path(lib_env), Path(dados_env)
    else:
        lib = Path(espeakng_loader.get_library_path())
        dados = Path(espeakng_loader.get_data_path())
        if not str(dados).isascii():
            espelho = _espelhar_ascii(lib, dados)
            if espelho is not None:
                lib, dados = espelho

    if not lib.exists() or not dados.exists():
        return "caminhos do espeak não existem: %s" % lib

    # O phonemizer lê estas variáveis na importação.
    os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = str(lib)
    os.environ["PHONEMIZER_ESPEAK_PATH"] = str(lib.parent)
    os.environ["ESPEAK_DATA_PATH"] = str(dados)

    # E o wrapper guarda o caminho numa variável de classe própria.
    try:
        from phonemizer.backend.espeak.wrapper import EspeakWrapper

        EspeakWrapper.set_library(str(lib))
        try:
            EspeakWrapper.set_data_path(str(dados))
        except Exception:
            # versões antigas não têm o setter; a env var cobre
            pass
    except Exception as e:  # noqa: BLE001
        return "phonemizer não aceitou o caminho: %s" % str(e)[:80]

    # O phonemizer memoriza a lista de idiomas na PRIMEIRA consulta, em
    # cache de classe (lru_cache). Se algum import já a montou com a DLL
    # antiga — a do caminho com acento, que falha ao ler os dados — a
    # lista fica vazia e "pt-br" passa a ser recusado, mesmo com o
    # espeak agora correto. O sintoma engana: chamar EspeakBackend(
    # 'pt-br') na mão funciona, mas via Kokoro falha, porque o Kokoro
    # importa antes. Limpar o cache força a releitura.
    try:
        from phonemizer.backend import EspeakBackend

        for alvo in (EspeakBackend.supported_languages,
                     getattr(EspeakBackend, "_all_languages", None)):
            if alvo is not None and hasattr(alvo, "cache_clear"):
                alvo.cache_clear()
    except Exception:
        pass

    return str(dados)


SSL = _corrigir_ssl()
ESPEAK = _corrigir_espeak()


def reaplicar_espeak() -> str:
    """Reaplica o fix DEPOIS de importar kokoro/misaki.

    `misaki/espeak.py` executa, no nível do módulo:

        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        EspeakWrapper.set_data_path(espeakng_loader.get_data_path())

    ou seja, o simples `import kokoro` desfaz a correção e reaponta o
    espeak para o caminho com acento, que não consegue ler os dados.
    Sintoma medido: `is_supported_language('pt-br')` vira True antes do
    import e False depois dele — e a lista de idiomas fica vazia porque
    a DLL não abre `phontab`.

    Por isso o fix roda duas vezes: antes (para o caso geral) e depois
    do import (para desfazer o que o misaki fez).
    """
    return _corrigir_espeak()

if __name__ == "__main__":
    print("ssl    :", SSL)
    print("espeak :", ESPEAK)
