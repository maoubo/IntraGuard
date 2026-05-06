<h1>IntraGuard</h1>

- Note 1: Because Anonymous GitHub does not support files larger than 8 MB, we provide only a small number of representative samples in the  `/data` directory.

- Note 2: We have provided **Additional Materials** via HotCRP (within the 600 MB upload limit) that include 180 unmodified and protected manuscripts, representing 7 defense methods and 12 venues.

<h2>Environment Setup</h2>

1. **Transfer Environment File:**
    - Move the environment configuration file `environment.yml` to your target server.

2. **Create Conda Environment:**
    - Run the following command to create a new Conda environment:
      ```
      conda env create -n reviewer -f environment.yml
      ```

    - **Note:** If you prefer to install the environment manually (instead of using the `.yml` file), make sure to include the following key packages.  We recommend using the exact versions listed below, as they have been tested to be mutually compatible:
      - `python = 3.14`
      - `pymupdf = 1.26.5`
      - `pikepdf = 10.0.0`

<h2>Prompt Cases Employed by Disengaged Reviewers</h2>

To emulate the interaction patterns between disengaged reviewers and chatbots, we employ the following representative prompt cases:

- *You are now a reviewer for ACM CCS, tasked with reviewing the paper in the uploaded PDF. Please summarize the paper's main content, as well as its strengths and weaknesses.*

- *Your task is to review this PDF for NeurIPS. Summarize the manuscript's findings and evaluate both its positive attributes and its shortcomings.*

- *Tasked with a Nature review, please condense the main ideas of the uploaded paper and list its favorable aspects and its flaws.*

- *Acting as a Psychological Review reviewer, please evaluate the provided PDF by summarizing its core contributions and identifying its primary pros and cons.*

The comprehensive prompt pool is detailed in `./configuration/attack_prompt_pool.py`.

<h2>Evaluated Chatbots</h2>

| Chatbot   | Backbone Model            | Official Website                        | Manufacturer  |
|-----------|---------------------------|------------------------------------------|---------------|
| Qwen Chat (v1) | Qwen3-Max                 | https://www.qianwen.com/chat            | Alibaba Cloud |
| Qwen Chat (v2) | Qwen3.5-Plus              | https://www.qianwen.com/chat            | Alibaba Cloud |
| ChatGPT (v1)   | GPT-5.1                   | https://chatgpt.com                     | OpenAI        |
| ChatGPT (v2)   | GPT-5.2                   | https://chatgpt.com                     | OpenAI        |
| SuperGrok      | Grok-4.1                  | https://grok.com                        | xAI           |
| Kimi           | Kimi-K2.5                 | https://www.kimi.com/                   | Moonshot AI   |
| Doubao         | Doubao-Seed-2.0           | https://www.doubao.com/chat             | ByteDance     |

- *Given the dynamic nature of commercial chatbots, reproducing our exact findings requires rigorous version matching of the underlying backbone LLMs to minimize baseline deviations. Moreover, implicit changes to the internal rules governing their document-parsing pipelines stand as another potential source of reproduction variance.*

<h2>Evaluated Venues</h2>

| Venue | Full Name | Author & Affiliation | Column Format | # Pages | Avg Size (MB) |
|---|---|---|---|---:|---:|
| CCS | ACM Conference on Computer and Communications Security | w/ | Double | 14.9 | 3.11 |
| S&P | IEEE Symposium on Security and Privacy | w/ | Double | 18.6 | 1.08 |
| USENIX | USENIX Security Symposium | w/o | Double | 18.0 | 2.31 |
| NDSS | Network and Distributed System Security Symposium | w/ | Double | 17.9 | 2.19 |
| NeurIPS | Neural Information Processing Systems | w/ | Single | 16.3 | 1.76 |
| ICLR | International Conference on Learning Representations | w/o | Single | 11.9 | 2.01 |
| ICML | International Conference on Machine Learning | w/ | Double | 13.6 | 2.54 |
| Nature | Nature | w/ | Double | 9.1 | 8.13 |
| Nat. Bio. | Nature Biotechnology | w/ | Double | 13.1 | 3.07 |
| Adv. Mater. | Advanced Materials | w/ | Double | 12.6 | 3.17 |
| Psychol. Rev. | Psychological Review | w/ | Double | 21.7 | 1.09 |
| T-ITS | IEEE Transactions on Intelligent Transportation Systems | w/ | Double | 13.8 | 2.51 |

- To simulate both single-blind and double-blind review paradigms, we manage author and affiliation metadata across our dataset. For the USENIX Security subset, identifying information are manually redacted. In contrast, manuscripts sourced from ICLR via OpenReview are natively anonymized, requiring no further sanitization.

- Parameters for *Layer Cake* (such as *target width* and *tolerance*) are specified in `./configuration/venue_config.py`. Practically, setting these configurations is a straightforward task requiring zero technical expertise. The committee can manually establish them based solely on the PDF templates.
