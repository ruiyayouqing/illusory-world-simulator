document.addEventListener('alpine:init', () => {
  Alpine.store('app', {
    settingsOpen: false,
    v10PanelOpen: false,
    whoIsWhoOpen: false,
    mapOpen: false,
    loading: false,
    toast: '',
    toastType: 'info',
    toastTimeout: null,

    showToast(message, type = 'info', duration = 3000) {
      this.toast = message
      this.toastType = type
      if (this.toastTimeout) clearTimeout(this.toastTimeout)
      this.toastTimeout = setTimeout(() => {
        this.toast = ''
      }, duration)
    },

    openSettings() {
      this.settingsOpen = true
      if (typeof loadSettings === 'function') loadSettings()
    },

    closeSettings() {
      this.settingsOpen = false
    },

    async saveSettings() {
      if (typeof saveSettings === 'function') {
        await saveSettings()
        this.showToast('设置已保存', 'success')
      }
    }
  })

  Alpine.data('settingsPanel', () => ({
    config: {
      llm: { api_key: '', base_url: 'https://token-plan-cn.xiaomimimo.com/v1', model_name: 'mimo-V2.5-Pro' },
      cheap_llm: { enabled: false, api_key: '', base_url: '', model_name: '' },
      dialogue_llm: { enabled: false, api_key: '', base_url: '', model_name: '' },
      image: { api_key: '', base_url: 'https://api.siliconflow.cn/v1/images/generations', model_name: 'Kwai-Kolors/Kolors' },
      // [v10.5] 文本向量嵌入模型配置
      embedding: { api_key: '', base_url: 'https://api.siliconflow.cn/v1', model_name: 'BAAI/bge-m3' },
      ui: { theme: 'obsidian', font_size: 'medium', strip_gray_narrative: true },
      npc_info_visibility: 'immersive',
      fixed_prompt: { content: '', enabled: true },
      game: { narrative_style: '章回体', narrative_perspective: 'second', economy_enabled: false }
    },
    llmProfiles: [],
    imageProfiles: [],
    selectedLlmProfile: '',
    selectedImageProfile: '',
    styleDescription: '',
    customStyleVisible: false,

    async init() {
      await this.loadConfig()
    },

    async loadConfig() {
      try {
        const res = await api('GET', '/api/config/raw')
        this.config = { ...this.config, ...res }
        if (res.llm) this.config.llm = { ...this.config.llm, ...res.llm }
        if (res.cheap_llm) this.config.cheap_llm = { ...this.config.cheap_llm, ...res.cheap_llm }
        if (res.dialogue_llm) this.config.dialogue_llm = { ...this.config.dialogue_llm, ...res.dialogue_llm }
        if (res.image) this.config.image = { ...this.config.image, ...res.image }
        if (res.embedding) this.config.embedding = { ...this.config.embedding, ...res.embedding }
        if (res.ui) this.config.ui = { ...this.config.ui, ...res.ui }
        if (res.fixed_prompt) this.config.fixed_prompt = { ...this.config.fixed_prompt, ...res.fixed_prompt }
        if (res.game) this.config.game = { ...this.config.game, ...res.game }
        this.llmProfiles = res.profiles?.llm || []
        this.imageProfiles = res.profiles?.image || []
        this.updateStyleDescription()
        this.customStyleVisible = this.config.game?.narrative_style === '自定义'
        this.$watch('config.game.narrative_style', (val) => {
          this.customStyleVisible = val === '自定义'
          this.updateStyleDescription()
        })
      } catch (e) {
        console.error('加载配置失败', e)
      }
    },

    updateStyleDescription() {
      const styles = {
        '章回体': '以章回体小说风格撰写，语言半文半白，节奏舒缓，注重铺垫和悬念。风格参考《三言二拍》《水浒传》的白话文。',
        '半古半文': '文言句式与白话叙事交融，类似《明朝那些事儿》或《琅琊榜》的风格。句式简练有力，偶用典故，但不晦涩。',
        '大白话': '现代口语化叙事，轻松幽默，像朋友在讲故事。短句为主，偶尔吐槽，贴近当代网文读者的阅读习惯。',
        '严肃文学': '冷峻克制的文学风格，类似余华、莫言。注重细节描写和心理刻画，语言凝练，情感内敛。',
        '网文爽文': '快节奏网文风格，爽点密集，系统提示频繁。数据化呈现，升级打怪。语言直白有力，每段都有钩子。',
        '诗化散文': '意境优先的散文风格，类似迟子建的作品。注重景物描写和氛围营造，语言优美，富有诗意。'
      }
      this.styleDescription = styles[this.config.game?.narrative_style] || ''
    },

    previewTheme() {
      if (typeof applyThemeConfig === 'function') {
        applyThemeConfig({ theme: this.config.ui.theme })
      }
    },

    async saveAll() {
      Alpine.store('app').loading = true
      try {
        const styleName = this.config.game?.narrative_style
        const customText = styleName === '自定义' ? (this.$refs.customStyle?.value || '') : ''
        const narrativePerspective = this.config.game?.narrative_perspective || 'second'
        await api('POST', '/api/narrative-style', { style_name: styleName, custom_text: customText, narrative_perspective: narrativePerspective })

        // [P3-8] 使用共享模块构建请求体
        const body = buildFullSettingsBody(this.config)
        await api('POST', '/api/full-settings', body)
        if (typeof applyThemeConfig === 'function') applyThemeConfig(this.config.ui)
        Alpine.store('app').closeSettings()
        Alpine.store('app').showToast('设置已保存', 'success')
      } catch (e) {
        Alpine.store('app').showToast('保存失败: ' + e.message, 'error')
      } finally {
        Alpine.store('app').loading = false
      }
    }
  }))

  Alpine.data('v10Panel', () => ({
    isOpen: false,
    input: '',
    processing: false,
    lastCommand: '',
    commandHistory: [],
    suggestions: [
      { cmd: '/who', desc: '📜 打开名人谱' },
      { cmd: '/map', desc: '🗺️ 打开地图' },
      { cmd: '/save', desc: '💾 手动存档' },
      { cmd: '/time', desc: '⏰ 推进时间' },
      { cmd: '/rest', desc: '😴 休息' },
      { cmd: '/inventory', desc: '🎒 查看背包' },
      { cmd: '/stats', desc: '📊 查看属性' },
      { cmd: '/quests', desc: '📋 查看任务' },
      { cmd: '/relations', desc: '👥 人物关系' },
      { cmd: '/news', desc: '📰 世界新闻' },
    ],

    submitCommand() {
      const cmd = this.input.trim()
      if (!cmd) return
      this.commandHistory.unshift(cmd)
      if (this.commandHistory.length > 50) this.commandHistory.pop()
      this.lastCommand = cmd
      this.processing = true

      const cmdLower = cmd.toLowerCase()
      if (cmdLower === '/who' || cmdLower === '/whoswho') {
        if (typeof openWhoIsWho === 'function') openWhoIsWho()
        this.close()
      } else if (cmdLower === '/map') {
        if (typeof openMap === 'function') openMap()
        this.close()
      } else if (cmdLower === '/save') {
        if (typeof doSave === 'function') doSave()
        Alpine.store('app').showToast('存档完成', 'success')
        this.close()
      } else {
        if (typeof doCustom === 'function') {
          document.getElementById('ci').value = cmd
          doCustom()
        }
        this.close()
      }

      this.input = ''
      this.processing = false
    },

    useSuggestion(cmd) {
      this.input = cmd + ' '
      this.$refs.cmdInput?.focus()
    },

    close() {
      this.isOpen = false
      this.input = ''
    },

    openPanel() {
      this.isOpen = true
      this.$nextTick(() => {
        this.$refs.cmdInput?.focus()
      })
    }
  }))

  window.AlpineStore = Alpine.store
})
