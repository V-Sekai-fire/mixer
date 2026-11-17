<img align="left" width="auto" height="auto" src="doc/images/Logo_90_A.png">

# Ubisoft Mixer - Blender Add-on

</br>

**Mixer is NOT SUPPORTED ANYMORE - it has been archived**

</br>

**Mixer is a Blender addon for real time collaboration in Blender. It allows multiple Blender users to work on the same scene at the same time.
 It was developed by the R&D department of Ubisoft Animation Studio.**

<p align="center">
  <a href="https://v-sekai-fire.github.io/mixer" title="Consult the online documentation" target="_blank">
  <img src="doc/images/Documentation.png" width="160" />
  </a>
  &nbsp;&nbsp;&nbsp;
  <a href="https://github.com/V-Sekai-fire/mixer/releases/latest" title="Download latest version">
  <img src="doc/images/Download.png" width="160" />
  </a>
  &nbsp;&nbsp;&nbsp;
  <a href="https://github.com/V-Sekai-fire/mixer/issues" title="Report and follow issues">
  <img src="doc/images/Issues.png" width="160" />
  </a>
</p>



![Mixer screenshot](docs/img/home_mixer.png)

## Disclaimer
>**This tool is NOT SUPPORTED ANYMORE**
>
>Up to December 2021 this tool was supported for LTS version of Blender 2.93 and for Blender 3.0.
>
>It was designed in a production context and was used as an experimental tool. With the end of the project developing it Mixer is not maintained anymore.
>
>You can use is as is. In spite of all our efforts to make it reliable, it may in some circumstances corrupt your Blender scenes data.
Be aware that neither Ubisoft nor Ubisoft employees can be taken as responsible in such cases. Use it at your own risks.
>

## Features

Refer to the documentation for details about current [features and limitations](https://v-sekai-fire.github.io/mixer/getting-started/features.html).

## Documentation and Usage

Documentation is available at the web site https://v-sekai-fire.github.io/mixer/ to :

- [download and install](https://v-sekai-fire.github.io/mixer/getting-started/install.html),
- [test](https://v-sekai-fire.github.io/mixer/getting-started/first-steps.html) locally,
- then [get connected](https://v-sekai-fire.github.io/mixer/collaborate/get-connected.html) and [work together](https://v-sekai-fire.github.io/mixer/collaborate/work-together.html)

## Broadcaster

The broadcaster now lives in [mixer-broadcaster](https://github.com/V-Sekai-fire/mixer-broadcaster) (Elixir, HTTP/3 WebTransport only). `mixer/broadcaster/apps/server.py` is deprecated and will be removed once the add-on defaults to the relay; the client modules in `mixer/broadcaster` stay.

## Updates

Mixer is archived, so this repository is not related to Ubisoft activities anymore.

## Support

Support is on the [issue tracker](https://github.com/V-Sekai-fire/mixer/issues) of this repository, a fork of Ubisoft's archived [Mixer](https://github.com/ubisoft/mixer).

## Community

The online documentation lists the [YouTube videos](https://v-sekai-fire.github.io/mixer/community/on_youtube.html) posted by community members.

## Feedback and contribution

Post your feedback and discuss your ideas in the [issues](https://github.com/V-Sekai-fire/mixer/issues) of this repository.

You can also get involved into the development of Mixer, discover how by reading these [contribution guidelines](doc/README.md).

## License and copyright

The original code is Copyright (C) 2020 Ubisoft.

All code of the `mixer` package is under the GPLv3 license except code of the `mixer.broadcaster` sub-package, which is under the MIT license.
