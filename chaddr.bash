# chaddr(1) completion script

_chaddr_profile_dir() {
    if [[ -n ${CHADDR_PROFILE_DIR:-} && -d ${CHADDR_PROFILE_DIR} ]]; then
        printf '%s\n' "${CHADDR_PROFILE_DIR}"
        return
    fi
    if [[ -d ${HOME}/.config/chaddr/profile ]]; then
        printf '%s\n' "${HOME}/.config/chaddr/profile"
        return
    fi
    if [[ -d ./profile ]]; then
        printf '%s\n' "./profile"
        return
    fi
    if [[ -d /usr/share/chaddr/profile ]]; then
        printf '%s\n' "/usr/share/chaddr/profile"
    fi
}

_chaddr_profiles() {
    local dir
    dir=$(_chaddr_profile_dir) || return
    compgen -G "${dir}/*" -X '*/' 2>/dev/null | xargs -n1 basename 2>/dev/null
}

_chaddr() {
    local cur prev words cword split
    _init_completion -s || return

    case "${prev}" in
        -c|--config)
            _filedir
            return
            ;;
        --proxy|--apply|--apply-ipv4|--apply-ipv6|--old-ip)
            return
            ;;
    esac

    if [[ ${cur} == -* ]]; then
        COMPREPLY=($(compgen -W '
            -c --config --proxy --diagnose --renew --apply --apply-ipv4
            --apply-ipv6 --old-ip -v --verbose --no-gui --help
        ' -- "${cur}"))
        return
    fi

    if [[ ${cword} -eq 1 || ${words[1]} != -* ]]; then
        mapfile -t COMPREPLY < <(compgen -W "$(_chaddr_profiles)" -- "${cur}")
    fi
}

complete -F _chaddr chaddr
