import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Calendar } from '@/components/ui/calendar';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { 
  Calendar as CalendarIcon, 
  Mic, 
  Languages, 
  Play, 
  Download, 
  Copy, 
  Trash2,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Music,
  Clock,
  User,
  FileText
} from 'lucide-react';
import { format } from 'date-fns';
import { ro } from 'date-fns/locale';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import './App.css';

interface VideoData {
  id: string;
  url: string;
  title: string;
  createdAt: Date;
  duration: string;
  status: 'pending' | 'processing' | 'completed' | 'error';
  transcription?: string;
  language: string;
}

const languages = [
  { value: 'auto', label: 'Detectare automată', flag: '🌐' },
  { value: 'ru', label: 'Rusă', flag: '🇷🇺' },
  { value: 'ro', label: 'Română', flag: '🇷🇴' },
  { value: 'ro-md', label: 'Română (Moldova)', flag: '🇲🇩' },
];

const getApiBase = () => {
  const envBase = import.meta.env.VITE_API_BASE as string | undefined;
  if (envBase) {
    return envBase;
  }
  const host = window.location.hostname === 'localhost' ? '127.0.0.1' : window.location.hostname;
  return `http://${host}:5001`;
};
const apiBase = getApiBase();

function App() {
  const [username, setUsername] = useState('');
  const [dateRange, setDateRange] = useState<{ from?: Date; to?: Date }>({});
  const [selectedLanguage, setSelectedLanguage] = useState('auto');
  const [isLoading, setIsLoading] = useState(false);
  const [videos, setVideos] = useState<VideoData[]>([]);
  const [overallProgress, setOverallProgress] = useState(0);

  // Încărcare videoclipuri din perioada selectată via backend
  const fetchVideos = async () => {
    if (!username) {
      toast.error('Te rugăm să introduci numele de utilizator TikTok');
      return;
    }
    if (!dateRange.from || !dateRange.to) {
      toast.error('Te rugăm să selectezi perioada');
      return;
    }

    setIsLoading(true);
    
    try {
      const response = await fetch(`${apiBase}/fetch-videos`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          username,
          start_date: dateRange.from.toISOString(),
          end_date: dateRange.to.toISOString(),
        }),
      });

      const responseText = await response.text();
      let data: any;
      try {
        data = JSON.parse(responseText);
      } catch {
        throw new Error(`Răspuns invalid de la server: ${responseText.slice(0, 200)}`);
      }

      if (!response.ok) {
        throw new Error(data?.error || `Eroare server: ${response.status}`);
      }

      if (!Array.isArray(data.videos)) {
        toast.error('Răspuns invalid de la server.');
        setIsLoading(false);
        return;
      }

      const formattedVideos: VideoData[] = data.videos.map((v: any) => ({
        ...v,
        createdAt: new Date(v.createdAt),
        language: selectedLanguage,
      }));

      setVideos(formattedVideos);
      if (formattedVideos.length === 0) {
        toast.info('Nu am găsit videoclipuri în perioada selectată.');
      } else {
        toast.success(`${formattedVideos.length} videoclipuri găsite`);
      }
    } catch (error: any) {
      const message = error?.message || 'Nu s-a putut conecta la serverul local.';
      toast.error(message);
      console.error(error);
    } finally {
      setIsLoading(false);
    }
  };

  // Transcrierea audio reală via backend
  const transcribeVideo = async (videoId: string) => {
    const video = videos.find(v => v.id === videoId);
    if (!video) return;

    setVideos(prev => prev.map(v => 
      v.id === videoId ? { ...v, status: 'processing' } : v
    ));

    try {
      const payload: Record<string, string> = {
        video_url: video.url,
      };
      if (selectedLanguage !== 'auto') {
        payload.language = selectedLanguage;
      }

      const response = await fetch(`${apiBase}/transcribe`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      const responseText = await response.text();
      let data: any;
      try {
        data = JSON.parse(responseText);
      } catch {
        throw new Error(`Răspuns invalid de la server: ${responseText.slice(0, 200)}`);
      }

      if (!response.ok || data.error) {
        const serverError = data?.error || `Eroare server: ${response.status}`;
        toast.error(`Eroare la transcriere: ${serverError}`);
        setVideos(prev => prev.map(v => 
          v.id === videoId ? { ...v, status: 'error' } : v
        ));
        return;
      }

      setVideos(prev => prev.map(v => 
        v.id === videoId ? { 
          ...v, 
          status: 'completed',
          transcription: data.transcription
        } : v
      ));

      updateOverallProgress();
      toast.success('Transcriere finalizată!');
    } catch (error: any) {
      const message = error?.message || 'Eroare de conexiune la serverul de transcriere.';
      toast.error(message);
      setVideos(prev => prev.map(v => 
        v.id === videoId ? { ...v, status: 'error' } : v
      ));
    }
  };

  // Transcriere toate videoclipurile
  const transcribeAll = async () => {
    const pendingVideos = videos.filter(v => v.status === 'pending');
    if (pendingVideos.length === 0) {
      toast.info('Nu există videoclipuri în așteptare');
      return;
    }

    for (const video of pendingVideos) {
      await transcribeVideo(video.id);
    }
  };

  const updateOverallProgress = () => {
    if (videos.length === 0) {
      setOverallProgress(0);
      return;
    }
    const completed = videos.filter(v => v.status === 'completed').length;
    setOverallProgress((completed / videos.length) * 100);
  };

  const copyTranscription = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success('Transcriere copiată în clipboard');
  };

  const downloadTranscription = (video: VideoData) => {
    if (!video.transcription) return;
    
    const blob = new Blob([video.transcription], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transcriere_${video.id}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    
    toast.success('Transcriere descărcată');
  };

  const exportCsv = () => {
    if (videos.length === 0) {
      toast.info('Nu există videoclipuri pentru export.');
      return;
    }

    const escapeCsv = (value: string | undefined) => {
      const safe = value ?? '';
      const needsQuotes = /[",\n]/.test(safe);
      const escaped = safe.replace(/"/g, '""');
      return needsQuotes ? `"${escaped}"` : escaped;
    };

    const header = ['id', 'url', 'title', 'transcription'].join(',');
    const rows = videos.map((video) => [
      escapeCsv(video.id),
      escapeCsv(video.url),
      escapeCsv(video.title),
      escapeCsv(video.transcription),
    ].join(','));

    const csvContent = [header, ...rows].join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `tiktok_transcriptions_${Date.now()}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    toast.success('Export CSV generat.');
  };

  const removeVideo = (videoId: string) => {
    setVideos(prev => prev.filter(v => v.id !== videoId));
    updateOverallProgress();
    toast.success('Videoclip eliminat');
  };

  const getStatusIcon = (status: VideoData['status']) => {
    switch (status) {
      case 'completed':
        return <CheckCircle2 className="h-5 w-5 text-green-500" />;
      case 'processing':
        return <RefreshCw className="h-5 w-5 text-blue-500 animate-spin" />;
      case 'error':
        return <AlertCircle className="h-5 w-5 text-red-500" />;
      default:
        return <Clock className="h-5 w-5 text-gray-400" />;
    }
  };

  const getStatusBadge = (status: VideoData['status']) => {
    switch (status) {
      case 'completed':
        return <Badge variant="default" className="bg-green-500">Finalizat</Badge>;
      case 'processing':
        return <Badge variant="default" className="bg-blue-500">În procesare</Badge>;
      case 'error':
        return <Badge variant="destructive">Eroare</Badge>;
      default:
        return <Badge variant="secondary">În așteptare</Badge>;
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 p-6">
      <div className="max-w-6xl mx-auto space-y-6">
        {/* Header */}
        <div className="text-center space-y-2">
          <h1 className="text-4xl font-bold text-slate-900 flex items-center justify-center gap-3">
            <Mic className="h-10 w-10 text-pink-500" />
            TikTok Audio Transcriber
          </h1>
          <p className="text-slate-600 text-lg">
            Transcrie automat clipurile audio de pe TikTok în limba rusă, română sau română cu slang moldovenesc
          </p>
        </div>

        {/* Configuration Card */}
        <Card className="shadow-lg">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <User className="h-5 w-5" />
              Configurare Cont
            </CardTitle>
            <CardDescription>
              Introdu datele contului TikTok și selectează perioada pentru transcriere
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Username Input */}
            <div className="space-y-2">
              <Label htmlFor="username">Nume utilizator TikTok</Label>
              <div className="flex gap-2">
                <span className="flex items-center px-3 bg-slate-100 border border-r-0 rounded-l-md text-slate-600">
                  @
                </span>
                <Input
                  id="username"
                  placeholder="nume_utilizator"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="rounded-l-none"
                />
              </div>
            </div>

            {/* Date Range */}
            <div className="space-y-2">
              <Label>Perioada</Label>
              <div className="flex gap-4 flex-wrap">
                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      className={cn(
                        "w-[200px] justify-start text-left font-normal",
                        !dateRange.from && "text-muted-foreground"
                      )}
                    >
                      <CalendarIcon className="mr-2 h-4 w-4" />
                      {dateRange.from ? format(dateRange.from, 'PPP', { locale: ro }) : 'Data început'}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start">
                    <Calendar
                      mode="single"
                      selected={dateRange.from}
                      onSelect={(date) => setDateRange(prev => ({ ...prev, from: date }))}
                      initialFocus
                    />
                  </PopoverContent>
                </Popover>

                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      className={cn(
                        "w-[200px] justify-start text-left font-normal",
                        !dateRange.to && "text-muted-foreground"
                      )}
                    >
                      <CalendarIcon className="mr-2 h-4 w-4" />
                      {dateRange.to ? format(dateRange.to, 'PPP', { locale: ro }) : 'Data sfârșit'}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start">
                    <Calendar
                      mode="single"
                      selected={dateRange.to}
                      onSelect={(date) => setDateRange(prev => ({ ...prev, to: date }))}
                      initialFocus
                    />
                  </PopoverContent>
                </Popover>
              </div>
            </div>

            {/* Language Selection */}
            <div className="space-y-2">
              <Label htmlFor="language" className="flex items-center gap-2">
                <Languages className="h-4 w-4" />
                Limba pentru transcriere
              </Label>
              <Select value={selectedLanguage} onValueChange={setSelectedLanguage}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Selectează limba" />
                </SelectTrigger>
                <SelectContent>
                  {languages.map((lang) => (
                    <SelectItem key={lang.value} value={lang.value}>
                      <span className="flex items-center gap-2">
                        <span>{lang.flag}</span>
                        <span>{lang.label}</span>
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-sm text-slate-500">
                {selectedLanguage === 'ro-md' && 'Include suport pentru slang moldovenesc și rusisme'}
              </p>
            </div>

            {/* Fetch Button */}
            <Button 
              onClick={fetchVideos} 
              disabled={isLoading}
              className="w-full bg-gradient-to-r from-pink-500 to-purple-600 hover:from-pink-600 hover:to-purple-700"
            >
              {isLoading ? (
                <>
                  <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                  Se încarcă videoclipurile...
                </>
              ) : (
                <>
                  <Music className="mr-2 h-4 w-4" />
                  Încarcă videoclipuri
                </>
              )}
            </Button>
          </CardContent>
        </Card>

        {/* Progress Overview */}
        {videos.length > 0 && (
          <Card className="shadow-lg">
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span className="flex items-center gap-2">
                  <FileText className="h-5 w-5" />
                  Progres General
                </span>
                <span className="text-2xl font-bold text-slate-700">
                  {Math.round(overallProgress)}%
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Progress value={overallProgress} className="h-3" />
              <div className="flex justify-between mt-2 text-sm text-slate-600">
                <span>{videos.filter(v => v.status === 'completed').length} finalizate</span>
                <span>{videos.filter(v => v.status === 'pending').length} în așteptare</span>
                <span>{videos.filter(v => v.status === 'processing').length} în procesare</span>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Videos List */}
        {videos.length > 0 && (
          <Card className="shadow-lg">
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Music className="h-5 w-5" />
                    Videoclipuri Găsite
                  </CardTitle>
                  <CardDescription>
                    {videos.length} videoclipuri în perioada selectată
                  </CardDescription>
                </div>
                <div className="flex items-center gap-2">
                  <Button 
                    onClick={transcribeAll}
                    disabled={videos.every(v => v.status !== 'pending')}
                    className="bg-gradient-to-r from-green-500 to-emerald-600 hover:from-green-600 hover:to-emerald-700"
                  >
                    <Play className="mr-2 h-4 w-4" />
                    Transcrie toate
                  </Button>
                  <Button 
                    onClick={exportCsv}
                    variant="outline"
                  >
                    Export CSV
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[500px]">
                <div className="space-y-4">
                  {videos.map((video) => (
                    <div key={video.id}>
                      <div className="flex items-start justify-between p-4 bg-slate-50 rounded-lg">
                        <div className="flex-1 space-y-2">
                          <div className="flex items-center gap-3">
                            {getStatusIcon(video.status)}
                            <span className="font-medium">{video.title}</span>
                            {getStatusBadge(video.status)}
                          </div>
                          <div className="flex items-center gap-4 text-sm text-slate-500">
                            <span className="flex items-center gap-1">
                              <Clock className="h-4 w-4" />
                              {video.duration}
                            </span>
                            <span>
                              {format(video.createdAt, 'dd MMM yyyy', { locale: ro })}
                            </span>
                            <span className="flex items-center gap-1">
                              <Languages className="h-4 w-4" />
                              {languages.find(l => l.value === video.language)?.label}
                            </span>
                          </div>
                          
                          {/* Transcription Display */}
                          {video.transcription && (
                            <div className="mt-3 p-3 bg-white border rounded-md">
                              <p className="text-sm text-slate-700 whitespace-pre-wrap">
                                {video.transcription}
                              </p>
                            </div>
                          )}
                        </div>

                        <div className="flex items-center gap-2 ml-4">
                          {video.status === 'pending' && (
                            <Button
                              size="sm"
                              onClick={() => transcribeVideo(video.id)}
                              variant="outline"
                            >
                              <Play className="h-4 w-4" />
                            </Button>
                          )}
                          {video.transcription && (
                            <>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => copyTranscription(video.transcription!)}
                              >
                                <Copy className="h-4 w-4" />
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => downloadTranscription(video)}
                              >
                                <Download className="h-4 w-4" />
                              </Button>
                            </>
                          )}
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => removeVideo(video.id)}
                            className="text-red-500 hover:text-red-700 hover:bg-red-50"
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </div>
                      <Separator className="my-2" />
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        )}

        {/* Info Card */}
        <Card className="shadow-lg bg-gradient-to-r from-blue-50 to-indigo-50 border-blue-200">
          <CardContent className="p-6">
            <div className="flex items-start gap-4">
              <div className="p-3 bg-blue-100 rounded-full">
                <Languages className="h-6 w-6 text-blue-600" />
              </div>
              <div>
                <h3 className="font-semibold text-blue-900">Suport Multi-Limbă</h3>
                <p className="text-blue-700 mt-1">
                  Aplicația suportă transcrierea în trei variante lingvistice:
                </p>
                <ul className="mt-2 space-y-1 text-sm text-blue-600">
                  <li className="flex items-center gap-2">
                    <span>🇷🇺</span>
                    <span><strong>Limba rusă</strong> - transcriere standard</span>
                  </li>
                  <li className="flex items-center gap-2">
                    <span>🇷🇴</span>
                    <span><strong>Limba română</strong> - transcriere standard</span>
                  </li>
                  <li className="flex items-center gap-2">
                    <span>🇲🇩</span>
                    <span><strong>Română (Moldova)</strong> - include suport pentru slang moldovenesc și rusisme</span>
                  </li>
                </ul>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export default App;
