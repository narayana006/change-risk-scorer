# change-risk-scorer

> Score the risk of a PR or deploy by blast radius, config changes, timing and recent incidents, and post it as a PR comment.

**Status:** 🚧 Early stage — design and roadmap published, implementation in progress. Stars, issues and ideas welcome.

## Why

Risky changes look the same as safe ones in a PR list until they cause an incident.

## Planned features

- [ ] Blast-radius estimation from touched paths and services
- [ ] Config and infra change detection
- [ ] Time-of-day / freeze-window awareness
- [ ] Recent incidents in the same area
- [ ] PR comment with score and reasons

## Intended usage

```
- uses: narayana006/change-risk-scorer@v1
```

_The interface above is the target design and may change._

## Roadmap

1. Core implementation of the first feature set
2. Examples and tests
3. CI and first tagged release
4. Docs and integrations

## Contributing

Contributions are welcome. Open an issue to discuss an idea or pick an unchecked item from the feature list.

## License

[MIT](LICENSE)
