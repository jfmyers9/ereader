-- Mounted-device layout: KOReader uses ./data/dict on Kindle.
-- Presets switch language explicitly; they do not change book-specific preferences.
return {
    set = {
        dicts_order = {
            ["./data/dict/gcide/stardict.ifo"] = 1,
            ["./data/dict/WordNet (r) 1.7/comn_dictd03_wn.ifo"] = 2,
            ["./data/dict/wiktionnaire-fr/reader.dict-fr.ifo"] = 3,
            ["./data/dict/French-English dictionary/french-english.ifo"] = 4,
        },
    },
    seed = {
        dict_presets = {
            ["English (ereader)"] = {
                enabled_dict_names = {
                    ["GNU Collaborative International Dictionary of English"] = true,
                    ["WordNet (r) 1.7"] = true,
                },
            },
            ["French (ereader)"] = {
                enabled_dict_names = {
                    ["reader.dict FR"] = true,
                    ["French-English dictionary"] = true,
                },
            },
        },
    },
}
