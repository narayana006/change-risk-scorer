# change-risk-scorer

Score the risk of a PR or deploy by blast radius, config changes, timing and recent incidents, and post it as a PR comment.

> **Project status: planning. There is no working code in this repository yet.**
> This README describes what the project will do. Nothing below can be installed or run today.
> Features are ticked off in the checklist as they are built.

## What it is

A GitHub Action and command-line tool.

**The problem it solves:** Risky changes look the same as safe ones in a PR list until they cause an incident.

## Who it is for

Teams who want risky changes to stand out before they are merged or deployed.

## How it will work

1. The action runs on each pull request.
2. It looks at which services and config the change touches, when it is being shipped and whether that area had recent incidents.
3. It posts a risk score with the reasons behind it.

## Planned features

Unticked items are not built yet.

- [ ] Blast-radius estimation from touched paths and services
- [ ] Config and infra change detection
- [ ] Time-of-day / freeze-window awareness
- [ ] Recent incidents in the same area
- [ ] PR comment with score and reasons

## Planned usage

This is the intended interface, shown so you can judge the design. **It does not work yet**, and names may change before the first release.

```yaml
- uses: narayana006/change-risk-scorer@v1
```

## Contributing

Feedback on the design is the most useful contribution right now. Open an issue to suggest a feature, question an approach or describe your use case. Code contributions are welcome once the first version lands.

## License

[MIT](LICENSE)
