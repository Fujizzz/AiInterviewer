# Corpus expansion, batch 2: per-source table

Files = distinct source pages, junk = chunks dropped by the junk filter, residue = total flags from `python -m rag.residue` (mostly `long_line` in Wikipedia/notebooks).

| source | domain | licence | files | chunks | junk dropped | residue |
|---|---|---|---|---|---|---|
| android-developers-docs | mobile | Apache-2.0 | 188 | 1584 | 0 | 2 |
| appium-docs | testing | Apache-2.0 | 23 | 77 | 0 | 1 |
| basicsr-docs | computer-vision | Apache-2.0 | 9 | 35 | 3 | 1 |
| bevy-book | game-dev | MIT | 38 | 260 | 0 | 0 |
| box2d-manual | game-dev | MIT | 9 | 111 | 0 | 0 |
| colyseus-docs | game-dev | MIT | 95 | 663 | 0 | 0 |
| cs231n-notes | deep-learning-theory | MIT | 20 | 233 | 6 | 49 |
| cucumber-docs | testing | MIT | 30 | 182 | 0 | 1 |
| cypress-docs | testing | MIT | 73 | 1206 | 11 | 3 |
| deep-rl-class | reinforcement-learning | Apache-2.0 | 30 | 99 | 0 | 0 |
| detectron2-docs | computer-vision | Apache-2.0 | 11 | 50 | 0 | 0 |
| devicetree-specification | embedded | Apache-2.0 | 6 | 81 | 0 | 0 |
| diffusers-docs | generative-models | Apache-2.0 | 43 | 293 | 1 | 0 |
| diffusion-models-class | generative-models | Apache-2.0 | 4 | 32 | 1 | 2 |
| engineering-playbook-testing | testing | CC-BY-4.0 | 22 | 148 | 4 | 0 |
| ffmpeg-wiki | computer-vision | CC-BY-SA-3.0 | 12 | 131 | 0 | 1 |
| firebase-cloud-messaging-docs | mobile | CC-BY-4.0 | 9 | 81 | 0 | 1 |
| flecs-docs | game-dev | MIT | 12 | 230 | 0 | 0 |
| flutter-website | mobile | CC-BY-3.0 | 140 | 913 | 3 | 0 |
| freertos-kernel-book | embedded | CC-BY-SA-4.0 | 13 | 302 | 1 | 0 |
| godot-docs | game-dev | CC-BY-3.0 | 214 | 1419 | 1 | 0 |
| great-expectations-docs | data-engineering | Apache-2.0 | 18 | 38 | 2 | 0 |
| gstreamer-tutorials | computer-vision | CC-BY-SA-4.0 | 24 | 207 | 0 | 0 |
| hf-llm-course-post-training | llm-post-training | Apache-2.0 | 33 | 235 | 2 | 1 |
| hikaricp-pool-sizing | databases | Apache-2.0 | 1 | 22 | 1 | 0 |
| hypothesis-docs | testing | MPL-2.0 | 19 | 71 | 0 | 0 |
| jest-docs | testing | MIT | 22 | 351 | 0 | 0 |
| jmeter-manual | testing | Apache-2.0 | 15 | 115 | 0 | 1 |
| jolt-physics-architecture | game-dev | MIT | 1 | 60 | 0 | 9 |
| junit5-user-guide | testing | EPL-2.0 | 58 | 215 | 1 | 0 |
| kalman-bayesian-filters-book | computer-vision | CC-BY-4.0 | 13 | 371 | 0 | 29 |
| kornia-docs | computer-vision | Apache-2.0 | 18 | 90 | 0 | 3 |
| kotlin-docs | mobile | Apache-2.0 | 99 | 849 | 2 | 0 |
| kotlin-multiplatform-docs | mobile | Apache-2.0 | 85 | 694 | 6 | 0 |
| langchain-docs | llm-engineering | MIT | 18 | 311 | 0 | 0 |
| learnopengl | game-dev | CC-BY-NC-4.0 | 44 | 439 | 0 | 30 |
| lightly-docs | deep-learning-theory | MIT | 12 | 22 | 0 | 1 |
| linux-kernel-docs | embedded | GPL-2.0 | 143 | 1063 | 7 | 0 |
| linux-kernel-labs | embedded | GPL-2.0 | 10 | 206 | 0 | 0 |
| lm-evaluation-harness-docs | llm-post-training | MIT | 10 | 113 | 0 | 0 |
| locust-docs | testing | MIT | 16 | 91 | 0 | 0 |
| mmagic-docs | computer-vision | Apache-2.0 | 67 | 344 | 1 | 35 |
| mmdetection-docs | computer-vision | Apache-2.0 | 27 | 216 | 6 | 0 |
| okhttp-docs | mobile | Apache-2.0 | 9 | 49 | 0 | 0 |
| opencv-docs | computer-vision | Apache-2.0 | 111 | 478 | 4 | 1 |
| pandera-docs | data-science | MIT | 15 | 106 | 0 | 0 |
| paper-all-in-one-deflicker | computer-vision | CC-BY-4.0 | 1 | 18 | 0 | 5 |
| paper-basicvsr | computer-vision | CC-BY-4.0 | 1 | 21 | 0 | 1 |
| paper-basicvsr-plusplus | computer-vision | CC-BY-4.0 | 1 | 18 | 0 | 0 |
| paper-bge-m3 | rag | CC-BY-4.0 | 1 | 37 | 0 | 12 |
| paper-chain-of-thought | llm-engineering | CC-BY-4.0 | 1 | 60 | 0 | 1 |
| paper-constitutional-ai | llm-post-training | CC-BY-4.0 | 1 | 49 | 0 | 4 |
| paper-dalle2 | generative-models | CC-BY-4.0 | 1 | 26 | 0 | 12 |
| paper-detr | computer-vision | CC0-1.0 | 1 | 48 | 0 | 1 |
| paper-dino | deep-learning-theory | CC-BY-4.0 | 1 | 49 | 0 | 0 |
| paper-dit | generative-models | CC-BY-4.0 | 1 | 34 | 0 | 9 |
| paper-dpo | llm-post-training | CC-BY-4.0 | 1 | 42 | 0 | 10 |
| paper-dreambooth | generative-models | CC-BY-4.0 | 1 | 41 | 0 | 12 |
| paper-e2fgvi | computer-vision | CC-BY-NC-SA-4.0 | 1 | 27 | 0 | 0 |
| paper-fcos | computer-vision | CC-BY-NC-SA-4.0 | 1 | 25 | 0 | 4 |
| paper-flowformer | computer-vision | CC-BY-4.0 | 1 | 21 | 0 | 1 |
| paper-gmflow | computer-vision | CC-BY-4.0 | 1 | 24 | 0 | 6 |
| paper-gptq | ml-efficiency | CC-BY-4.0 | 1 | 28 | 0 | 3 |
| paper-gqa | llm-engineering | CC-BY-4.0 | 1 | 17 | 0 | 1 |
| paper-hh-rlhf | llm-post-training | CC-BY-4.0 | 1 | 95 | 0 | 13 |
| paper-ipo | llm-post-training | CC-BY-NC-SA-4.0 | 1 | 27 | 0 | 5 |
| paper-kto | llm-post-training | CC-BY-SA-4.0 | 1 | 49 | 0 | 0 |
| paper-lima | llm-post-training | CC-BY-4.0 | 1 | 34 | 0 | 0 |
| paper-llama | llm-engineering | CC-BY-4.0 | 1 | 44 | 0 | 0 |
| paper-loftr | computer-vision | CC-BY-4.0 | 1 | 22 | 0 | 0 |
| paper-mae | deep-learning-theory | CC-BY-4.0 | 1 | 38 | 0 | 2 |
| paper-mistral | llm-engineering | CC-BY-4.0 | 1 | 10 | 0 | 0 |
| paper-mixtral | llm-engineering | CC-BY-4.0 | 1 | 15 | 0 | 0 |
| paper-mobilesam | computer-vision | CC-BY-NC-SA-4.0 | 1 | 20 | 0 | 13 |
| paper-propainter | computer-vision | CC-BY-4.0 | 1 | 24 | 0 | 4 |
| paper-qlora | llm-post-training | CC-BY-4.0 | 1 | 60 | 0 | 6 |
| paper-real-basicvsr | computer-vision | CC-BY-4.0 | 1 | 19 | 0 | 0 |
| paper-restormer | computer-vision | CC-BY-NC-SA-4.0 | 1 | 28 | 0 | 4 |
| paper-rlaif | llm-post-training | CC-BY-4.0 | 1 | 45 | 0 | 2 |
| paper-sdxl | generative-models | CC-BY-4.0 | 1 | 27 | 0 | 3 |
| paper-self-consistency | llm-engineering | CC-BY-4.0 | 1 | 35 | 0 | 1 |
| paper-self-instruct | llm-post-training | CC-BY-4.0 | 1 | 51 | 0 | 3 |
| paper-self-rag | rag | CC-BY-4.0 | 1 | 50 | 4 | 9 |
| paper-smoothquant | ml-efficiency | CC-BY-4.0 | 1 | 31 | 0 | 0 |
| paper-speculative-decoding | ml-serving | CC-BY-4.0 | 1 | 25 | 0 | 1 |
| paper-strongsort | computer-vision | CC-BY-4.0 | 1 | 26 | 0 | 0 |
| paper-swin-transformer | computer-vision | CC-BY-4.0 | 1 | 40 | 0 | 4 |
| paper-tokenflow | computer-vision | CC-BY-4.0 | 1 | 24 | 0 | 6 |
| paper-trackformer | computer-vision | CC-BY-4.0 | 1 | 45 | 0 | 0 |
| paper-tree-of-thoughts | llm-engineering | CC-BY-4.0 | 1 | 21 | 0 | 5 |
| paper-video-swin | computer-vision | CC-BY-4.0 | 1 | 26 | 0 | 1 |
| paper-videomae | computer-vision | CC-BY-4.0 | 1 | 31 | 0 | 7 |
| paper-xmem | computer-vision | CC-BY-NC-SA-4.0 | 1 | 40 | 0 | 0 |
| paper-yolov10 | computer-vision | CC-BY-4.0 | 1 | 32 | 0 | 18 |
| paper-zephyr | llm-post-training | CC-BY-4.0 | 1 | 22 | 0 | 3 |
| peft-docs | llm-post-training | Apache-2.0 | 26 | 177 | 0 | 11 |
| pgbouncer-docs | databases | ISC | 2 | 71 | 0 | 0 |
| playwright-docs | testing | Apache-2.0 | 54 | 497 | 1 | 0 |
| pydantic-docs | web-backend | MIT | 16 | 208 | 2 | 0 |
| pytest-docs | testing | MIT | 35 | 248 | 0 | 0 |
| pytorch-notes | ml-engineering | BSD-3-Clause | 13 | 189 | 0 | 0 |
| react-native-website | mobile | CC-BY-4.0 | 142 | 872 | 2 | 4 |
| robot-framework-userguide | testing | Apache-2.0 | 18 | 338 | 0 | 0 |
| rust-embedded-book | embedded | Apache-2.0 OR MIT | 35 | 149 | 3 | 2 |
| rust-embedded-discovery | embedded | Apache-2.0 OR MIT | 64 | 142 | 0 | 0 |
| sahi-docs | computer-vision | MIT | 7 | 53 | 1 | 0 |
| scikit-image-docs | computer-vision | BSD-3-Clause | 112 | 227 | 0 | 0 |
| selenium-docs | testing | Apache-2.0 | 50 | 199 | 3 | 0 |
| smol-course | llm-post-training | Apache-2.0 | 15 | 156 | 6 | 0 |
| spinning-up | reinforcement-learning | MIT | 11 | 78 | 1 | 0 |
| supervision-docs | computer-vision | MIT | 22 | 114 | 3 | 2 |
| swift-book | mobile | Apache-2.0 | 30 | 572 | 0 | 1 |
| swift-evolution-proposals | mobile | Apache-2.0 | 13 | 358 | 9 | 11 |
| testcontainers-docs | testing | MIT | 21 | 70 | 1 | 0 |
| testing-library-docs | testing | MIT | 20 | 83 | 1 | 0 |
| torchvision-docs | computer-vision | BSD-3-Clause | 26 | 141 | 1 | 0 |
| trl-docs | llm-post-training | Apache-2.0 | 26 | 373 | 1 | 12 |
| ultralytics-docs | computer-vision | AGPL-3.0 | 65 | 980 | 0 | 2 |
| uva-dl-notebooks | deep-learning-theory | MIT | 15 | 181 | 0 | 37 |
| verl-docs | llm-post-training | Apache-2.0 | 14 | 82 | 1 | 2 |
| vitest-docs | testing | MIT | 23 | 216 | 1 | 3 |
| vulkan-guide | game-dev | CC-BY-4.0 | 57 | 566 | 6 | 4 |
| wikipedia-algorithms-cs | algorithms | CC-BY-SA-4.0 | 103 | 1044 | 0 | 122 |
| wikipedia-cv-video | computer-vision | CC-BY-SA-4.0 | 103 | 929 | 1 | 130 |
| wikipedia-embedded | embedded | CC-BY-SA-4.0 | 90 | 903 | 2 | 102 |
| wikipedia-game-dev | game-dev | CC-BY-SA-4.0 | 97 | 974 | 0 | 136 |
| wikipedia-generative-llm | generative-models | CC-BY-SA-4.0 | 56 | 612 | 2 | 66 |
| wikipedia-mobile | mobile | CC-BY-SA-4.0 | 57 | 460 | 1 | 63 |
| wikipedia-testing-qa | testing | CC-BY-SA-4.0 | 73 | 449 | 0 | 35 |
| yocto-docs | embedded | CC-BY-SA-2.0-UK | 66 | 622 | 1 | 0 |
| zephyr-docs | embedded | Apache-2.0 | 180 | 943 | 25 | 1 |

## Skipped / reference_only (not chunked)

| source | licence | reason |
|---|---|---|
| gstreamer-app-dev-manual | Open Publication License v1.0 (gst-docs README; not ingested to stay c | Application Developer Manual and Plugin Writer's Guide of gst-docs are OPL v1.0 "for historical reasons"; the  |
| pact-docs | Unknown - the docs.pact.io repository has no LICENSE file and its READ | Pact docs (consumer-driven contract testing) could not be ingested: no licence is stated in the repository. Th |
| k6-docs | Unknown - the k6-docs repository has no LICENSE file (GitHub reports n | Grafana k6 docs: no licence file in the docs repository, so not ingested. Locust and the JMeter manual are the |
| testng-docs | Unknown - the website repository has no LICENSE file (GitHub reports n | TestNG documentation site repository states no licence; JUnit 5 user guide (EPL-2.0) is ingested instead. |
| google-testing-blog | Blog posts; not ingested (blogs are out of scope and per-post licensin | Blogs are excluded by the corpus rules. Test pyramid and flaky-test material comes from the engineering playbo |
| game-programming-patterns | CC-BY-NC-ND-4.0 (prose, repository LICENSE); MIT (code) | No-derivatives clause: chunking is a derivative, so the book (object pool, game loop, component, flyweight, st |
| the-book-of-shaders | All rights reserved (repository LICENSE forbids hosting, display or us | Explicit all-rights-reserved licence. LearnOpenGL (CC BY-NC), Godot shader docs and the Vulkan Guide are inges |
| gaffer-on-games | Copyright (c) Glenn Fiedler; no open licence stated | Game networking articles (snapshot interpolation, client-side prediction, lockstep) are copyrighted with no re |
| redblobgames-pathfinding | Copyright (c) Red Blob Games; no open licence for the text stated | A* tutorials are copyrighted; Wikipedia (A* search algorithm, navigation mesh, pathfinding), cp-algorithms and |
| unity-unreal-docs | Proprietary vendor documentation (Unity, Epic Games) | Unity manual / DOTS / Netcode for GameObjects and Unreal docs are proprietary and are not ingested. Flecs, Bev |
| apple-developer-docs | Proprietary vendor documentation (Apple) | SwiftUI, UIKit, Core Data, Instruments and APNs documentation are proprietary and not ingested. The Swift book |
| misra-c-guidelines | Proprietary (paid standard) | MISRA C rules are proprietary. Wikipedia "MISRA C" (rule categories in prose), CMSIS-Core notes and the Embedd |
| linux-device-drivers-3 | CC-BY-NC-SA-2.0 (book chapters) | Published as PDF chapters only; no PDF loader in the pipeline. Linux kernel Documentation (driver-api, i2c, sp |
| u-boot-docs | GPL-2.0-or-later (repository) | Not ingested: command and board-specific reference pages dominate doc/; low interview value next to Yocto, dev |
| bootlin-training-slides | CC-BY-SA-3.0 (slides) | Beamer / PDF slides (LaTeX sources), not parsable by the available loaders. Yocto docs and kernel Documentatio |
| cmsis-docs | Apache-2.0 | Doxygen-centric API pages (CMSIS-Core NVIC/intrinsics, CMSIS-RTOS2) are mostly generated API listings; not ing |
| ffmpeg-official-docs | LGPL-2.1 / GPL (texinfo man pages) | ffmpeg-all.html is a single giant command reference; the FFmpeg wiki (CC BY-SA 3.0) community guides are inges |
| segment-anything-detr-raft-repos | Apache-2.0 / BSD-3-Clause (READMEs) | Not ingested: READMEs of SAM, SAM2, DETR, RAFT, ByteTrack are install/usage notes with checkpoint tables; the  |
| mockito-docs | MIT | Not ingested: the documentation lives in the Mockito.java javadoc, which is an API reference dump. JUnit 5, py |
| test-pyramid-articles | Copyright (c) Martin Fowler / Ham Vocke; no open licence | Test pyramid articles (martinfowler.com, Google Testing Blog) are copyrighted. The test pyramid is covered by  |
| arxiv-papers-reference-only-batch2 | arXiv.org perpetual non-exclusive licence (or CC BY-NC-ND) - redistrib | Papers checked on 2026-10-16 whose arXiv abs page shows the default non-exclusive licence (or CC BY-NC-ND): no |
