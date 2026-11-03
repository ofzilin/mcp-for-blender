# Terms and Conditions

This is a personal fork of [ahujasid/mcp-for-blender](https://github.com/ahujasid/mcp-for-blender),
and it is not affiliated with the Blender Foundation.

## Telemetry

**There is none.** This fork removed the telemetry, trajectory-recording and consent-prompt
modules (`telemetry.py`, `telemetry_decorator.py`, `trajectory.py`, `consent_prompt.py`) and
every call site in `server.py`. No usage record, prompt, generated code, screenshot, scene
metadata, or manual-edit record is sent anywhere, and no client-side opt-out is required.

The upstream terms and conditions describe the telemetry of the upstream project and do not
apply to this fork. They are available at
<https://github.com/ahujasid/mcp-for-blender/blob/main/TERMS_AND_CONDITIONS.md>.

The Blender addon (`addon.py`) is unmodified and still contains the upstream consent commands.
This server never calls them, so they collect nothing.

## Licensing

- The code in this repository is MIT licensed, as inherited from upstream. See [LICENSE](LICENSE).
- The Blender Manual corpus used by `search_manual` / `get_manual_page` is **not** part of this
  repository. It is copyright the Blender Documentation Team and licensed CC BY-SA 4.0; it is
  installed separately by the user, from <https://projects.blender.org/blender/blender-manual>.

## Your work

You keep all rights to everything you create. This fork collects nothing, so no license to your
content is granted or implied.
