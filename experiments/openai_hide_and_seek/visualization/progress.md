Original prompt: Okay launch and iterate lets try playing an actual game now (small). note that they can pick up and move around objects as needed and utilize them. from original openai paper

Updates:
- Confirmed online/offline: official OpenAI repo ships MuJoCo environment + saved policy weights, not a browser-ready policy runner or polished renderer.
- Native MuJoCo viewer currently segfaults on local macOS, so this visualization should play with small local policies first.

TODO:
- Replace fixed cinematic loop with finite-state hider/seeker policies.
- Keep `render_game_to_text` and `advanceTime(ms)` hooks for test automation.
