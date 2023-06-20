## Bot de Música para Discord

Este é um bot de música para Discord que permite reproduzir e gerenciar músicas em servidores. Com ele, você pode tocar suas faixas favoritas, criar playlists e controlar a reprodução diretamente no Discord.

## Instalação

Para utilizar este bot, é necessário instalar as seguintes bibliotecas:

```shell
pip install discord.py
pip install wavelink
pip install aiohttp
pip install humanize```

Certifique-se de ter o Python e o pip instalados em seu sistema antes de executar esses comandos.

## Configuração

Antes de executar o bot, você precisa configurar algumas informações no arquivo `config.py`. Abra o arquivo e preencha as seguintes informações:

- `TOKEN`: o token do seu bot do Discord. Você pode obter um token ao criar um bot na [Página de Desenvolvedores do Discord](https://discord.com/developers/applications).
- `PREFIX`: o prefixo que será usado para os comandos do bot. Por padrão, o prefixo é definido como `!`.

## Uso

Depois de configurar o bot, você pode executá-lo usando o seguinte comando:

Certifique-se de estar no diretório raiz do projeto ao executar o comando acima.

Após executar o bot, ele estará online no Discord e pronto para reproduzir músicas. Para obter uma lista de comandos disponíveis, digite `!help` no chat do Discord.

## Contribuição

Se você encontrar algum problema ou tiver sugestões de melhorias, sinta-se à vontade para abrir uma *issue* ou enviar um *pull request* neste repositório. Estamos abertos a contribuições!

## Licença

Este projeto está licenciado sob a [Licença MIT](LICENSE).
